from hashlib import md5
import base64
import json
import boto3
import os

lambda_client = boto3.client('lambda', region_name='us-east-1')
dynamo_client = boto3.client('dynamodb', region_name='us-east-1')

kv_cache_table = 'kv_cache'
acc_number = os.environ['acc_number']

def lambda_handler(event, context):
    deploy(event['target_function'], event['zip_file'], event['config'])

def deploy(function_name, b64_zip_str, config):
    zip_file = base64.b64decode(b64_zip_str)

    zip_hash = md5(zip_file).hexdigest()
    config_hash = md5(json.dumps(config, sort_keys=True).encode('utf-8')).hexdigest()

    zip_cache_key = '{}#zip-hash'.format(function_name)
    config_cache_key = '{}#config-hash'.format(function_name)

    # Check if function has changed
    zip_cache_query = query_kv_cache(zip_cache_key)

    # There already exists a cached entry for the function (meaning it exists!)
    if 'Item' in zip_cache_query:
        action = 'update'
        cached_zip_hash = zip_cache_query['Item']['value']['S']

    # If an entry was not found on the cache, let's check lambda itself. The
    # function may already exist, so we'd need to update it. Otherwise, we have
    # to create a new one.
    else:
        action = get_action(function_name)
        cached_zip_hash = ''

    if action == 'update':
        # Zip file hasn't changed
        if cached_zip_hash == zip_hash:
            print('{} zip hash hasn\'t changed'.format(function_name))

        # Function was updated
        else:
            # Update code
            resp = lambda_client.update_function_code(
                FunctionName=function_name,
                ZipFile=zip_file,
                Publish=True
            )

            if resp['ResponseMetadata']['HTTPStatusCode'] == 200:
                # Save new hash on cache
                update_kv_cache(zip_cache_key, zip_hash)

                print('{} code updated'.format(function_name))
            else:
                print('Error updating code {}: {}'.format(function_name, resp))

    # Function doesn't exist; let's create it
    else:
        resp = lambda_client.create_function(
            FunctionName=function_name,
            Runtime='python3.6',
            Role=derive_role(config, function_name),
            Code={'ZipFile': zip_file},
            Handler=config['handler'],
            Timeout=config['timeout'],
            MemorySize=config['memory'],
            Publish=True
        )

        if resp['ResponseMetadata']['HTTPStatusCode'] == 201:
            # Save new function and config on cache
            update_kv_cache(zip_cache_key, zip_hash)
            update_kv_cache(config_cache_key, config_hash)

            print('{} created'.format(function_name))
        else:
            print('Error creating function {}: {}'.format(function_name, resp))

        # No need to update the config; returning here...
        return

    config_cache_query = query_kv_cache(config_cache_key)

    if 'Item' in config_cache_query:
        cached_config_hash = config_cache_query['Item']['value']['S']
    else:
        cached_config_hash = ''

    # Config hasn't changed
    if cached_config_hash == config_hash:
        print('{} config hash hasn\'t changed'.format(function_name))

    # Config is different; update it
    else:
        # Update config
        resp = lambda_client.update_function_configuration(
            FunctionName=function_name,
            MemorySize=config['memory'],
            Timeout=config['timeout'],
            Handler=config['handler']
        )

        if resp['ResponseMetadata']['HTTPStatusCode'] == 200:
            # Save new hash on cache
            update_kv_cache(config_cache_key, config_hash)
            
            print('{} config updated'.format(function_name))
        else:
            print('Error updating config {}: {}'.format(function_name, resp))

def query_kv_cache(key):
    return dynamo_client.get_item(
        TableName=kv_cache_table,
        Key={'key': {'S': key}}
    )

def update_kv_cache(key, value):
    return dynamo_client.put_item(
        TableName=kv_cache_table,
        Item={'key': {'S': key}, 'value': {'S': value}}
    )

def derive_role(config, function_name):
    if 'role_name' in config:
        role_name = config['role_name']
    else:
        role_name = '_lambda_{}'.format(function_name)

    return 'arn:aws:iam::{}:role/{}'.format(acc_number, role_name)

def get_action(function_name):
    # Function does exist; just update its code/config
    try:
        lambda_client.get_function(
            FunctionName=function_name
        )

        return 'update'

    # Function does not exist; we should create it
    except lambda_client.exceptions.ResourceNotFoundException:
        return 'create'
