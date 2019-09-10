import boto3

def custom_input():
    # bucket configuration
    inputs = {}
    inputs['bucket_name'] = 'mordor-cloudformation-templates-v1'
    inputs['url'] = 'https://mordor-cloudformation-templates-v1.s3.ap-south-1.amazonaws.com/'
    inputs['region'] = 'ap-south-1'
    # environment
    inputs['environment'] = 'uat'
    inputs['account'] = 'mordor'
    inputs['available_azs'] = boto3.client('ec2', region_name=inputs['region'])
    return inputs

def prerequisites():
    # create s3 bucket
    return

def write_to_file(filename, data):
    with open(filename, 'w+') as f:
        f.write(data)
