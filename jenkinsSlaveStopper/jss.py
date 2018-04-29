import boto3

ec2_client = boto3.client('ec2', region_name='us-east-1')

def lambda_handler(event, context):
    tag = event['tag']

    query = ec2_client.describe_instances(
        Filters=[
            {
                'Name': 'tag:jenkins_slave_tag',
                'Values': [tag]
            }
        ],
    )['Reservations']

    print('Found {} matching instances on tag {}'.format(len(query), tag))

    for instance in query:
        data = instance['Instances'][0]
        state_code = data['State']['Code']
        instance_id = data['InstanceId']

        if state_code == 16 or state_code == 0:
            print('Terminating {}'.format(instance_id))
            ec2_client.terminate_instances(InstanceIds=[instance_id])
        else:
            print('Skipping because instance has code {}'.format(state_code))
