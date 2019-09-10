#!/usr/bin/env python
"""
EKS Configuration
"""
__author__ = "Navin S"

import boto3
from troposphere import Ref, Template, Tags, GetAtt
import troposphere.ec2 as ec2
import troposphere.eks as eks

def main():
    return