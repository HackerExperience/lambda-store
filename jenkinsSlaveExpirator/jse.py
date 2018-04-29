from datetime import datetime
import boto3

ec2_client = boto3.client('ec2', region_name='us-east-1')

def lambda_handler(_event, _context):
    query = ec2_client.describe_instances(
        Filters=[
            {
                'Name': 'tag-key',
                'Values': ['jenkins_slave_expiration_date']
            },
            {
                'Name': 'instance-state-code',
                'Values': ['0', '16']
            }
        ],
    )['Reservations']

    print('Found {} matching instances...'.format(len(query)))

    for instance in query:
        if len(instance['Instances']) > 1:
            print('Not sure what this means')

        data = instance['Instances'][0]

        expiration_date = None

        for tag in data['Tags']:
            if tag['Key'] == 'jenkins_slave_expiration_date':
                expiration_date = datetime.strptime(tag['Value'], '%Y-%m-%d %H:%M:%S.%f')
                break;

        if datetime.utcnow() >= expiration_date:
            instance_id = data['InstanceId']

            print('Terminating instance {}'.format(instance_id))

            ec2_client.terminate_instances(
                InstanceIds=[instance_id]
            )
