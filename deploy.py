import subprocess
import base64
import shutil
import json
import sys
import os
import boto3

lambda_client = boto3.client('lambda', region_name='us-east-1')
lambda_meta_deployer = 'lambdaMetaDeployer'

with_error = False

def bootstrap():
    # Cleanup and prepare packaging folder
    shutil.rmtree('_packages/', ignore_errors=True)
    os.makedirs('_packages')

    shutil.rmtree('_dependencies/', ignore_errors=True)
    os.makedirs('_dependencies', exist_ok=True)

def scan_folders():
    # Traverse the folder, detect functions and deploy each one individually
    for root, dirs, files in os.walk('.', topdown=False):
        if root.startswith('./.'):
            continue

        if root.startswith('./_'):
            continue

        if root == '.':
            continue

        # It's-a me, function!
        if 'config.json' in files:
            function_name = root[2:]

            if 'requirements.txt' in files:
                os.makedirs('_dependencies/{}'.format(function_name))

            deploy(function_name)

    if with_error:
        raise Exception('Some functions were not deployed')

def deploy(function_name):
    print('Packaging {}'.format(function_name))
    
    with open(function_name + '/config.json') as config_file:    
        config = json.load(config_file)

    validate_config(config)

    print('Using config: {}'.format(config))

    setup_dependencies(function_name)

    create_package(function_name, config)

    upsert_function(function_name, config)

def upsert_function(function_name, config):
    with open('_packages/{}.zip'.format(function_name), 'rb') as package:
        contents = package.read()
        b64_zip_str = base64.b64encode(contents).decode('utf-8')

        payload = {
            'zip_file': b64_zip_str,
            'config': config,
            'target_function': function_name
        }

        resp = lambda_client.invoke(
            FunctionName=lambda_meta_deployer,
            Payload=json.dumps(payload)
        )

        if resp['ResponseMetadata']['HTTPStatusCode'] != 200:
            print('Bad status code for {}: {}'.format(function_name, resp))
            with_error = True
        else:
            print('{} deployed\n\n'.format(function_name))

def create_package(function_name, config):
    # Zip application-specific stuff
    cmd1 = 'cd {0}; zip -X -r9 ../_packages/{0}.zip *'.format(function_name)
    subprocess.run(cmd1, check=True, shell=True)

    # Add dependencies to package (if they exist)
    if os.path.exists('_dependencies/{}/'.format(function_name)):
        cmd2 = 'cd _dependencies/{0}; zip -X -r9 ../../_packages/{0}.zip *'.format(function_name)
        subprocess.run(cmd2, check=True, shell=True)

    if not os.stat('_packages/{0}.zip'.format(function_name)):
        error('internal', 'error_creating_zip')

def setup_dependencies(function_name):
    if os.path.exists('{}/requirements.txt'.format(function_name)):
        cmd = 'pip3 install -r {0}/requirements.txt -t _dependencies/{0} '.format(function_name)
        subprocess.run(cmd, check=True, shell=True)

def validate_config(config):
    if not 'memory' in config:
        error('config', 'missing_memory')

    if config['memory'] % 128 not in [64, 0]:
        error('config', 'invalid_memory')

    if not 'timeout' in config:
        error('config', 'missing_timeout')

    if not 'handler' in config:
        error('config', 'missing_handler')

def error(major, minor):
    raise Exception(major + '_' + minor)

if __name__ == '__main__':
    bootstrap()

    if len(sys.argv) == 1:
        scan_folders()
    else:
        for function_name in sys.argv[1:]:
            deploy(function_name)
