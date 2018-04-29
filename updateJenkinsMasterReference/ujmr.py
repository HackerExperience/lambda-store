import os
from base64 import b64decode
import CloudFlare
import boto3

EMAIL = os.environ['UJMR_EMAIL']
API_KEY_ENC = os.environ['UJMR_API_KEY']
API_KEY = boto3.client('kms').decrypt(CiphertextBlob=b64decode(API_KEY_ENC))['Plaintext']

zone_name = 'hackerexperience.com'
external_name = 'ci.' + zone_name
internal_name = 'internal.' + external_name

# Authenticate with CF
cf = CloudFlare.CloudFlare(email=EMAIL, token=API_KEY)

def lambda_handler(event, context):

    # Event values (input)
    new_external_ip = event['external_ip']
    new_internal_ip = event['internal_ip']

    # Get zone
    zone_id = get_zone(cf, zone_name)[0]['id']

    # Get DNS records
    external_record_id = get_dns_record(cf, zone_id, external_name)[0]['id']
    internal_record_id = get_dns_record(cf, zone_id, internal_name)[0]['id']

    # Generate new DNS records
    new_external_record = gen_new_dns_record(external_name, new_external_ip)
    new_internal_record = gen_new_dns_record(internal_name, new_internal_ip, False)

    # Update DNS records
    update_record(cf, zone_id, external_record_id, new_external_record)
    update_record(cf, zone_id, internal_record_id, new_internal_record)

# Helper methods

def get_zone(cf, name):
    try:
        return cf.zones.get(params={'name': name})
    except CloudFlare.exceptions.CloudFlareAPIError as e:
        exit('/zones %d %s - api call failed' % (e, e))
    except Exception as e:
        exit('/zones.get - %s - api call failed' % (e))

def get_dns_record(cf, zone_id, name, dns_type = 'A'):
    try:
        params = {'name': name, 'match': 'all', 'type': dns_type}
        return cf.zones.dns_records.get(zone_id, params=params)
    except CloudFlare.exceptions.CloudFlareAPIError as e:
        exit('/zones/dns_records %s - %d %s - api call failed' % (name, e, e))

def update_record(cf, zone_id, record_id, new_record):
    try:
        return cf.zones.dns_records.put(zone_id, record_id, data=new_record)
    except CloudFlare.exceptions.CloudFlareAPIError as e:
        exit('/zones.dns_records.post %s - %d %s - api call failed' % (record_id, e, e))

def gen_new_dns_record(name, new_ip, proxied = True):
    return {
        'name': name,
        'type': 'A',
        'content': new_ip,
        'proxied': proxied
    }
