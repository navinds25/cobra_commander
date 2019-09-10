"""
VPC Configuration
"""
__author__ = "Navin S"

import boto3
from troposphere import Ref, Template, Tags, GetAtt
import troposphere.ec2 as ec2
import troposphere.logs as logs
from troposphere.iam import Policy, Role
from awacs.aws import Action, Allow, PolicyDocument, Principal, Statement
import yaml
import os
from cobra.cobra import write_to_file, custom_input

def get_subnet_mapping():
    current_dir = os.path.dirname(os.path.realpath(__file__))
    conf_file_path = os.path.join(current_dir, 'subnet_mapping.yml')
    with open(conf_file_path) as f:
        subnet_config = yaml.safe_load(f)['subnet_mapping']
    return subnet_config

def get_subnet_config(environment, account_name):
    subnet_config = {}
    subnet_mapping = get_subnet_mapping()
    env = subnet_mapping['environments'][environment]
    zones = boto3.client('ec2', region_name='ap-south-1').describe_availability_zones(Filters=[{'Name': 'state', 'Values': ['available']}])
    availableazs = [i['ZoneName'] for i in zones['AvailabilityZones']]
    for az in range(1, subnet_mapping['number_of_azs']+1):
        for tier in subnet_mapping['service_name_for_subnets']:
            for service in subnet_mapping['service_name_for_subnets'][tier]:
                service_number = subnet_mapping['service_name_for_subnets'][tier][service]
                subnet = '10.{}.{}{}.0/24'.format(env, service_number, az)
                subnet_id = '{0}{1}{2}{3}{4}'.format(
                    account_name,
                    environment.title(),
                    service.title(),
                    az,
                    tier.title()
                    )
                subnet_config[subnet_id] = {}
                subnet_config[subnet_id]['subnet'] = subnet
                subnet_config[subnet_id]['service'] = service
                subnet_config[subnet_id]['az_number'] = az
                subnet_config[subnet_id]['az_name'] = availableazs[az]
                subnet_config[subnet_id]['tier'] = tier
    return subnet_config

def create_vpc(t, env, env_number, subnet_mapping, subnet_config):
    '''
    Creates the VPC along with the subnets, IGW and RouteTables
    '''
    vpc_objects = {}
    vpc_objects['vpc'] = t.add_resource(ec2.VPC(
        "{}VPC".format(env.upper()),
        CidrBlock="10.{}.0.0/16".format(env_number),
        InstanceTenancy="default",
        Tags=Tags(Name="{}VPC".format(env.upper()))
        ))
    vpc_objects['igw'] = t.add_resource(ec2.InternetGateway("InternetGateway"))
    vpc_objects['igw_attachment'] = t.add_resource(ec2.VPCGatewayAttachment(
        "IGWAttachment",
        VpcId=Ref(vpc_objects['vpc']),
        InternetGatewayId=Ref(vpc_objects['igw']),
    ))

    # Create Subnets
    vpc_objects['subnets'] = {}
    vpc_objects['nat_eip'] = {}
    for subid in subnet_config:
        vpc_objects['subnets'][subid] = t.add_resource(
            ec2.Subnet(
                subid, CidrBlock=subnet_config[subid]['subnet'],
                VpcId=Ref(vpc_objects['vpc']),
                AvailabilityZone="{}".format(subnet_config[subid]['az_name']),
                Tags=Tags(Name="{}Subnet".format(subid))
                )
            )
        # Create NAT Gateways
        if subnet_config[subid]['service'] == 'nat':
            az = subnet_config[subid]['az_number']
            nat_eip_name = '{}{}NatEIP'.format(env.title(), az)
            vpc_objects['nat_eip'][nat_eip_name] = t.add_resource(ec2.EIP(nat_eip_name, Domain="vpc"))
            t.add_resource(ec2.NatGateway(
                '{}{}NatGW'.format(env.title(), az),
                AllocationId=GetAtt(vpc_objects['nat_eip'][nat_eip_name], 'AllocationId'),
                SubnetId=Ref(vpc_objects['subnets'][subid])
            ))
    return t, vpc_objects

def create_routes(t, env, vpc_objects, subnet_config, subnet_mapping):
    '''
    Takes template t and vpc_objects to add routes and security_groups
    '''
    # Create Route Tables
    vpc_objects['route_tables'] = {}
    for tier in subnet_mapping['service_name_for_subnets'].keys():
        for az in range(1, subnet_mapping['number_of_azs']+1):
            rt_name = "{}{}{}RT".format(env.title(), tier.title(), az)
            vpc_objects['route_tables'][rt_name] = t.add_resource(
                ec2.RouteTable(
                    rt_name,
                    VpcId=Ref(vpc_objects['vpc']),
                    Tags=Tags(Name=rt_name)
                    ))
            #add route for IGW in DMZ route table
            if tier == "dmz":
                t.add_resource(ec2.Route(
                    '{}{}{}IGW'.format(env.title(), tier.title(), az),
                    #DependsOn=Ref(vpc_objects['igw_attachment']),
                    GatewayId=Ref('InternetGateway'),
                    DestinationCidrBlock='0.0.0.0/0',
                    RouteTableId=Ref(vpc_objects['route_tables'][rt_name])
                    ))
            elif tier == "app" or tier == "internal":
                t.add_resource(ec2.Route(
                    '{}{}{}NAT'.format(env.title(), tier.title(), az),
                    #DependsOn=Ref(vpc_objects['igw_attachment']),
                    NatGatewayId=Ref('{}{}NatGW'.format(env.title(), az)),
                    DestinationCidrBlock='0.0.0.0/0',
                    RouteTableId=Ref(vpc_objects['route_tables'][rt_name])
                    ))

    for subid in subnet_config:
        tier = subnet_config[subid]['tier'].lower()
        az = subnet_config[subid]['az_number']
        route_table_name = '{}{}{}RT'.format(env.title(), tier.title(), az)
        #associate subnet with route table
        t.add_resource(ec2.SubnetRouteTableAssociation(
            '{}{}{}RTA'.format(route_table_name.title(), subid.title(), az),
            SubnetId=Ref(vpc_objects['subnets'][subid]),
            RouteTableId=Ref(vpc_objects['route_tables'][route_table_name])
        ))
    return t, vpc_objects

def flow_logs(t, vpc_objects):
    vpc_flow_log_role = t.add_resource(
        Role(
            "vpcflowlogrole",
            AssumeRolePolicyDocument=PolicyDocument(
                Statement=[
                    Statement(
                        Effect=Allow,
                        Action=[
                            Action("sts", "AssumeRole")
                        ],
                        Principal=Principal("Service", "vpc-flow-logs.amazonaws.com")
                    )
                ]
            ),
            Policies=[
                Policy(
                    PolicyName="vpc_flow_logs_policy",
                    PolicyDocument=PolicyDocument(
                        Id="vpc_flow_logs_policy",
                        Version="2012-10-17",
                        Statement=[
                            Statement(
                                Effect=Allow,
                                Action=[
                                 Action("logs", "CreateLogGroup"),
                                 Action("logs", "CreateLogStream"),
                                 Action("logs", "PutLogEvents"),
                                 Action("logs", "DescribeLogGroups"),
                                 Action("logs", "DescribeLogStreams")
                                ],
                                Resource=["arn:aws:logs:*:*:*"]
                            )
                        ]
                    )
                )
            ]
            )
    )
    t.add_resource(logs.LogGroup(
        'VPCLogGroup',
        LogGroupName='VPCFlowLog',
        #DependsOn="vpcflowlogrole",
        RetentionInDays=7
        ))
    t.add_resource(ec2.FlowLog(
        'VPCFlowLog',
        DeliverLogsPermissionArn=GetAtt(vpc_flow_log_role, "Arn"),
        LogGroupName='VPCFlowLog',
        ResourceId=Ref(vpc_objects['vpc']),
        ResourceType='VPC',
        #DependsOn="VPCLogGroup",
        TrafficType='ALL'
        ))
    return t, vpc_objects

def main():
    t = Template()
    inputs = custom_input()
    subnet_mapping = get_subnet_mapping()
    subnet_config = get_subnet_config(inputs['environment'], inputs['account'])
    print(subnet_config)
    env = inputs['environment']
    env_number = subnet_mapping['environments'][env]
    t, vpc_objects = create_vpc(t, env, env_number, subnet_mapping, subnet_config)
    t, vpc_objects = create_routes(t, env, vpc_objects, subnet_config, subnet_mapping)
    #print(t.to_json())
    t, vpc_objects = flow_logs(t, vpc_objects)
    write_to_file("{}_vpc".format(env), t.to_json())

if __name__ == '__main__':
    main()
