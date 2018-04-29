import json
from datetime import datetime, timedelta
import boto3

# Helper values

ec2_client = boto3.client('ec2', region_name='us-east-1')
dynamo_client = boto3.client('dynamodb', region_name='us-east-1')

default_small_instances = ['c5.large', 'm4.large', 'c4.large']
default_large_instances = ['c5.2xlarge', 'm4.2xlarge', 'c4.2xlarge']

all_instances = default_small_instances + default_large_instances

default_instance_map = {
    'large-1': default_large_instances,
    'large-2': default_large_instances,
    'large-3': default_large_instances,
    'small-1': default_small_instances,
    'small-2': default_small_instances,
    'small-3': default_small_instances,
}

role_map = {
    'helix': {
        'ami_id': 'ami-f109c38c',
        'snapshot_id': 'snap-0b8d60120f091d2c9',
        'instance_type': default_instance_map
    },
    'utils': {
        'ami_id': 'ami-a15fecde',
        'snapshot_id': 'snap-00895a0f5dd73a713',
        'instance_type': default_instance_map
    }
}

max_price_map = {
    'c5.large': 0.032,
    'm4.large': 0.031,
    'c4.large': 0.029,
    'c5.2xlarge': 0.125,
    'm4.2xlarge': 0.120,
    'c4.2xlarge': 0.115
}

all_azs = [
    'us-east-1a',
    'us-east-1b',
    'us-east-1c',
    'us-east-1d',
    'us-east-1e',
    'us-east-1f'
]

spot_price_cache_table = 'spot_price_cache'

def lambda_handler(event, context):
    role = event['role']
    size = event['size']
    tag = event['tag']
    max_duration = int(event['max_duration'])

    if max_duration >= 60:
        print('Max duration must no be >= 60 for {}-{}'.format(role, tag))
        max_duration = 60
    
    instance_id = launch_spot(role, size, tag, max_duration)

    return {
        'instance_id': instance_id
    }

# Helper methods

def launch_spot(role, size, tag, max_duration):
    spot = select_spot_instance(role, size)

    instance_type = spot['instance_type']
    max_price = spot['max_price']
    az = spot['az']

    expiration_date = datetime.utcnow() + timedelta(minutes = max_duration)

    # Create the spot request
    spot_request_id = ec2_client.request_spot_instances(
        ClientToken='{}-{}-{}'.format(role, size, tag),
        InstanceCount=1,
        LaunchSpecification=generate_launch_spec(role, spot, tag),
        SpotPrice=str(max_price),
        Type='one-time',
        ValidUntil=expiration_date.timestamp()
    )['SpotInstanceRequests'][0]['SpotInstanceRequestId']

    instance_id = get_instance_id_from_spot_request(spot_request_id)

    # Add tags on instance
    ec2_client.create_tags(
        Resources=[instance_id,],
        Tags=[
            {'Key': 'jenkins_slave_role', 'Value': role},
            {'Key': 'jenkins_slave_size', 'Value': size},
            {'Key': 'jenkins_slave_tag', 'Value': tag},
            {'Key': 'jenkins_slave_expiration_date', 'Value': str(expiration_date)},
            {'Key': 'Name', 'Value': 'slave-{}-{}'.format(role, tag)},
            {'Key': 'Stage', 'Value': 'Build'},
            {'Key': 'Role', 'Value': 'JenkinsSlave'}
        ]
    )

    return instance_id

def generate_spot_cache():
    result = {}
    now = datetime.utcnow()
    timestamp = now.replace(minute=0, second=0, microsecond=0)

    resp = dynamo_client.get_item(
        TableName=spot_price_cache_table,
        Key={
            'timestamp': {'S': str(timestamp)}
        }
    )

    # Found an entry on the cache
    if 'Item' in resp:
        return json.loads(resp['Item']['prices']['S'])

    # Generate price list
    # <rant> Yes, it's one request per instance-type per AZ. That's because AWS's
    # API (`describe-spot-price-history`) really really sucks. For example,
    # filtering the results wouldn't guarantee a result on all AZs, since unchanged
    # prices do not create datapoints on their timeseries (or create with a larger
    # interval). Hence the cache.</rant>
    for instance in all_instances:
        result[instance] = {'cheapest': {'price': 999}}

        for az in all_azs:
            # M5 family not available at `us-east-1e` AZ
            if instance.startswith('m5') and az == 'us-east-1e':
                continue

            price = float(ec2_client.describe_spot_price_history(
                ProductDescriptions=['Linux/UNIX'],
                InstanceTypes=[instance],
                AvailabilityZone=az,
                MaxResults=1
            )['SpotPriceHistory'][0]['SpotPrice'])

            result[instance][az] = price

            if price < result[instance]['cheapest']['price']:
                result[instance]['cheapest'] = {
                    'price': price,
                    'az': az
                }

    expiration_date = now.timestamp() + (24 * 60 * 60)

    # Save result on cache
    resp = dynamo_client.put_item(
        TableName=spot_price_cache_table,
        Item={
            'timestamp': {'S': str(timestamp)},
            'prices': {'S': json.dumps(result)},
            'expiration_date': {'N': str(expiration_date)}
        }
    )

    return result

def select_spot_instance(role, size):
    price_list = generate_spot_cache()

    possible_instances = role_map[role]['instance_type'][size]

    selected = None
    for instance in possible_instances:
        max_price = max_price_map[instance]
        cheapest = price_list[instance]['cheapest']

        if max_price > cheapest['price']:
            return {
                'instance_type': instance,
                'az': cheapest['az'],
                'max_price': max_price
            }

    # If reached here, then all possible instances are over the max price
    # Well there's nothing we can do but to pick one of them
    # In this case we pick the cheapest possible entry
    cheapest = None

    for instance in possible_instances:
        instance_cheapest = price_list[instance]['cheapest']

        if not cheapest or instance_cheapest['price'] < cheapest:
            cheapest = instance_cheapest['price']

            selected = {
                'instance_type': instance,
                'az': instance_cheapest['az'],
                'max_price': cheapest * 1.15
            }

    return cheapest

def generate_launch_spec(role, spot, tag):
    instance_type = spot['instance_type']
    max_price = spot['max_price']
    az = spot['az']

    ami_id = role_map[role]['ami_id']
    snapshot_id = role_map[role]['snapshot_id']

    subnet_id = from_az_get_subnet(az)

    if instance_type.startswith('m4'):
        ebs_optimized = False
    else:
        ebs_optimized = True

    return {
        'BlockDeviceMappings': [
            {
                'DeviceName': '/dev/sda1',
                'Ebs': {
                    'DeleteOnTermination': True,
                    'SnapshotId': snapshot_id,
                    'VolumeSize': 10,
                    'VolumeType': 'gp2'
                },
            },
        ],
        'EbsOptimized': ebs_optimized,
        'ImageId': ami_id,
        'InstanceType': instance_type,
        'NetworkInterfaces': [
            {
                'AssociatePublicIpAddress': True,
                'DeleteOnTermination': True,
                'DeviceIndex': 0,
                'Groups': [
                    'sg-2aedaf63',
                ],
                'SubnetId': subnet_id
            },
        ]
    }

# AWS Helpers

def get_instance_id_from_spot_request(spot_request_id):
    waiter = ec2_client.get_waiter('spot_instance_request_fulfilled')

    # Wait for request to be fulfilled
    waiter.wait(
        SpotInstanceRequestIds=[spot_request_id,],
        WaiterConfig={'Delay': 3, 'MaxAttempts': 10}
    )

    # Fetch instance id
    return ec2_client.describe_spot_instance_requests(
        SpotInstanceRequestIds=[spot_request_id]
    )['SpotInstanceRequests'][0]['InstanceId']

def from_az_get_subnet(az):
    if az == 'us-east-1a':
        return 'subnet-2c82724b'
    elif az == 'us-east-1b':
        return 'subnet-16fb1a38'
    elif az == 'us-east-1c':
        return 'subnet-e7ce65ad'
    elif az == 'us-east-1d':
        return 'subnet-2d836471'
    elif az == 'us-east-1e':
        return 'subnet-49fffc76'
    else:
        return 'subnet-abd154a4'

def dict_to_item(raw):
    """Transform a python element into a DynamoDB-compatible tuple"""
    if isinstance(raw, dict):
        return {
            'M': {
                k: dict_to_item(v)
                for k, v in raw.items()
            }
        }
    elif isinstance(raw, list):
        return {
            'L': [dict_to_item(v) for v in raw]
        }
    elif isinstance(raw, str):
        return {'S': raw}
    elif isinstance(raw, int):
        return {'N': str(raw)}
