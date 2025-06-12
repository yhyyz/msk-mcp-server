#!/usr/bin/env python3
"""
MSK Route53 MCP Server

This MCP server provides tools for managing MSK clusters with Route53 DNS records,
including creating custom domains and performing DR switching.
"""

import boto3
from typing import Dict, Optional, Any
from fastmcp import FastMCP
#from mcp_instance import mcp
mcp_custom_domain = FastMCP("This MCP server provides tools for managing MSK clusters with Route53 DNS records,including creating custom domains and performing DR switching.", 
             dependencies=["boto3", "botocore"])
#mcp_custom_domain=mcp

def msk_cluster_info(region: str, cluster_name: str) -> Dict[str, Any]:
    kafka_client = boto3.client('kafka', region_name=region)

    # Get cluster ARN
    response = kafka_client.list_clusters()
    cluster_arn = None

    for cluster in response['ClusterInfoList']:
        if cluster['ClusterName'] == cluster_name:
            cluster_arn = cluster['ClusterArn']
            break

    if not cluster_arn:
        return {"error": f"Cluster '{cluster_name}' not found in region {region}"}

    # Get detailed cluster information
    cluster_info = kafka_client.describe_cluster(ClusterArn=cluster_arn)

    return {
        "cluster_arn": cluster_arn,
        "cluster_info": cluster_info['ClusterInfo']
    }

@mcp_custom_domain.tool("get_msk_cluster_info")
def get_msk_cluster_info(region: str, cluster_name: str) -> Dict[str, Any]:
    """
    Get information about an MSK cluster.

    Args:
        region: AWS region where the cluster is located
        cluster_name: Name of the MSK cluster
    """
    return msk_cluster_info(region, cluster_name)

def msk_broker_info(region: str, cluster_name: str) -> Dict[str, Any]:
    """
    Get broker information for an MSK cluster.

    Args:
        region: AWS region where the cluster is located
        cluster_name: Name of the MSK cluster

    Returns:
        Dictionary containing broker information including bootstrap addresses
    """
    # First get the cluster ARN
    kafka_client = boto3.client('kafka', region_name=region)

    response = kafka_client.list_clusters()
    cluster_arn = None

    for cluster in response['ClusterInfoList']:
        if cluster['ClusterName'] == cluster_name:
            cluster_arn = cluster['ClusterArn']
            break

    if not cluster_arn:
        return {"error": f"Cluster '{cluster_name}' not found in region {region}"}

    # Get bootstrap broker information
    broker_info = kafka_client.get_bootstrap_brokers(ClusterArn=cluster_arn)

    # Parse broker addresses
    broker_addresses = []
    if 'BootstrapBrokerString' in broker_info and broker_info['BootstrapBrokerString']:
        broker_addresses = broker_info['BootstrapBrokerString'].split(',')

    broker_addresses_tls = []
    if 'BootstrapBrokerStringTls' in broker_info and broker_info['BootstrapBrokerStringTls']:
        broker_addresses_tls = broker_info['BootstrapBrokerStringTls'].split(',')

    broker_addresses_sasl_iam = []
    if 'BootstrapBrokerStringSaslIam' in broker_info and broker_info['BootstrapBrokerStringSaslIam']:
        broker_addresses_sasl_iam = broker_info['BootstrapBrokerStringSaslIam'].split(',')

    return {
        "cluster_arn": cluster_arn,
        "broker_addresses": broker_addresses,
        "broker_addresses_tls": broker_addresses_tls,
        "broker_addresses_sasl_iam": broker_addresses_sasl_iam,
        "raw_broker_info": broker_info
    }

@mcp_custom_domain.tool("get_msk_broker_info")
def get_msk_broker_info(region: str, cluster_name: str) -> Dict[str, Any]:
    """
    Get broker information for an MSK cluster.

    Args:
        region: AWS region where the cluster is located
        cluster_name: Name of the MSK cluster

    Returns:
        Dictionary containing broker information including bootstrap addresses
    """
    return msk_broker_info(region, cluster_name)

def vpc_for_msk_cluster(region: str, cluster_name: str) -> Dict[str, str]:
    # First get cluster info
    cluster_info = msk_cluster_info(region, cluster_name)

    if "error" in cluster_info:
        return cluster_info

    # Get subnet ID from cluster info
    try:
        subnet_id = cluster_info["cluster_info"]["BrokerNodeGroupInfo"]["ClientSubnets"][0]
    except (KeyError, IndexError):
        return {"error": "Could not retrieve subnet information from cluster"}

    # Get VPC ID from subnet
    ec2_client = boto3.client('ec2', region_name=region)
    response = ec2_client.describe_subnets(SubnetIds=[subnet_id])

    try:
        vpc_id = response['Subnets'][0]['VpcId']
        return {"vpc_id": vpc_id, "subnet_id": subnet_id}
    except (KeyError, IndexError):
        return {"error": "Could not retrieve VPC information from subnet"}

@mcp_custom_domain.tool("get_vpc_for_msk_cluster")
def get_vpc_for_msk_cluster(region: str, cluster_name: str) -> Dict[str, str]:
    """
    Get the VPC ID for an MSK cluster.

    Args:
        region: AWS region where the cluster is located
        cluster_name: Name of the MSK cluster

    Returns:
        Dictionary containing VPC information
    """
    return vpc_for_msk_cluster(region, cluster_name)

def route53_hosted_zone_info(zone_name: str) -> Dict[str, Any]:
    # Ensure zone name ends with a dot
    if not zone_name.endswith('.'):
        zone_name = f"{zone_name}."

    route53_client = boto3.client('route53')

    # Get hosted zone ID
    response = route53_client.list_hosted_zones()
    zone_id = None

    for zone in response['HostedZones']:
        if zone['Name'] == zone_name:
            zone_id = zone['Id'].replace('/hostedzone/', '')
            break

    if not zone_id:
        return {"error": f"Hosted zone '{zone_name}' not found"}

    # Get detailed zone information
    zone_info = route53_client.get_hosted_zone(Id=zone_id)

    return {
        "zone_id": zone_id,
        "zone_info": zone_info
    }


@mcp_custom_domain.tool("get_route53_hosted_zone_info")
def get_route53_hosted_zone_info(zone_name: str) -> Dict[str, Any]:
    """
    Get information about a Route53 hosted zone.

    Args:
        zone_name: Name of the hosted zone (e.g., "example.com.")

    Returns:
        Dictionary containing hosted zone information
    """
    return route53_hosted_zone_info(zone_name)

@mcp_custom_domain.tool("create_msk_custom_domain")
def create_msk_custom_domain(
    region: str,
    cluster_name: str,
    zone_name: str,
    vpc_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a custom domain for an MSK cluster by setting up Route53 records.

    Args:
        region: AWS region where the cluster is located
        cluster_name: Name of the MSK cluster
        zone_name: Name of the Route53 hosted zone to use (e.g., "kafka.example.com")
        vpc_id: Optional VPC ID (if not provided, will be auto-detected)

    Returns:
        Dictionary containing creation results
    """
    # Ensure zone name ends with a dot for Route53
    if not zone_name.endswith('.'):
        zone_name = f"{zone_name}."

    # Get VPC ID if not provided
    if not vpc_id:
        vpc_info = vpc_for_msk_cluster(region, cluster_name)
        if "error" in vpc_info:
            return vpc_info
        vpc_id = vpc_info["vpc_id"]

    # Get broker information
    broker_info = msk_broker_info(region, cluster_name)
    if "error" in broker_info:
        return broker_info

    # Get hosted zone information
    zone_info = route53_hosted_zone_info(zone_name)
    if "error" in zone_info:
        return zone_info

    zone_id = zone_info["zone_id"]

    # Associate VPC with hosted zone
    route53_client = boto3.client('route53')
    try:
        route53_client.associate_vpc_with_hosted_zone(
            HostedZoneId=zone_id,
            VPC={
                'VPCRegion': region,
                'VPCId': vpc_id
            }
        )
    except route53_client.exceptions.ConflictingDomainExists:
        # VPC is already associated, which is fine
        pass
    except Exception as e:
        return {"error": f"Failed to associate VPC with hosted zone: {str(e)}"}

    # Create DNS records for brokers
    broker_addresses = broker_info["broker_addresses"]

    changes = []
    for i, broker in enumerate(broker_addresses, 1):
        # Extract broker hostname (remove port)
        broker_host = broker.split(':')[0]

        # Create DNS record
        dns_name = f"broker{i}.{zone_name}"

        changes.append({
            'Action': 'UPSERT',
            'ResourceRecordSet': {
                'Name': dns_name,
                'Type': 'CNAME',
                'TTL': 300,
                'ResourceRecords': [
                    {'Value': broker_host}
                ]
            }
        })

    # Apply changes
    try:
        response = route53_client.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={'Changes': changes}
        )

        return {
            "success": True,
            "message": f"Successfully created DNS records for MSK cluster {cluster_name}",
            "broker_dns_names": [f"broker{i}.{zone_name[:-1]}" for i in range(1, len(broker_addresses) + 1)],
            "change_info": response['ChangeInfo']
        }
    except Exception as e:
        return {"error": f"Failed to create DNS records: {str(e)}"}

@mcp_custom_domain.tool("list_route53_resource_record_sets")
def list_route53_resource_record_sets(zone_name: str) -> Dict[str, Any]:
    """
    List all resource record sets in a Route53 hosted zone.

    Args:
        zone_name: Name of the hosted zone (e.g., "example.com.")

    Returns:
        Dictionary containing resource record sets
    """
    zone_info = route53_hosted_zone_info(zone_name=zone_name)
    zone_id = zone_info["zone_id"]
    route53_client = boto3.client('route53')
    response = route53_client.list_resource_record_sets(HostedZoneId=zone_id)
    return {
        "zone_info": zone_info,
        "resource_record_sets": response
    }

@mcp_custom_domain.tool("perform_dr_switch")
def perform_dr_switch(
    primary_region: str,
    primary_cluster_name: str,
    dr_region: str,
    dr_cluster_name: str,
    zone_name: str,
    primary_vpc_id: Optional[str] = None,
    dr_vpc_id: Optional[str] = None,
    activate_dr: bool = True
) -> Dict[str, Any]:
    """
    Perform a DR switch by updating Route53 records to point to either the primary or DR cluster.

    Args:
        primary_region: AWS region of the primary cluster
        primary_cluster_name: Name of the primary MSK cluster
        dr_region: AWS region of the DR cluster
        dr_cluster_name: Name of the DR MSK cluster
        zone_name: Name of the Route53 hosted zone
        primary_vpc_id: Optional VPC ID for primary cluster (auto-detected if not provided)
        dr_vpc_id: Optional VPC ID for DR cluster (auto-detected if not provided)
        activate_dr: If True, switch to DR cluster; if False, switch to primary cluster

    Returns:
        Dictionary containing switch results
    """
    # Determine active region and cluster based on activate_dr flag
    active_region = dr_region if activate_dr else primary_region
    active_cluster_name = dr_cluster_name if activate_dr else primary_cluster_name

    # Get VPC IDs if not provided
    if not primary_vpc_id:
        vpc_info = vpc_for_msk_cluster(primary_region, primary_cluster_name)
        if "error" in vpc_info:
            return vpc_info
        primary_vpc_id = vpc_info["vpc_id"]

    if not dr_vpc_id:
        vpc_info = vpc_for_msk_cluster(dr_region, dr_cluster_name)
        if "error" in vpc_info:
            return vpc_info
        dr_vpc_id = vpc_info["vpc_id"]

    active_vpc_id = dr_vpc_id if activate_dr else primary_vpc_id

    # Ensure zone name ends with a dot
    if not zone_name.endswith('.'):
        zone_name = f"{zone_name}."

    # Get hosted zone information
    zone_info = route53_hosted_zone_info(zone_name)
    if "error" in zone_info:
        return zone_info

    zone_id = zone_info["zone_id"]

    # Get broker information for active cluster
    broker_info = msk_broker_info(active_region, active_cluster_name)
    if "error" in broker_info:
        return broker_info

    # Associate active VPC with hosted zone
    route53_client = boto3.client('route53')
    try:
        route53_client.associate_vpc_with_hosted_zone(
            HostedZoneId=zone_id,
            VPC={
                'VPCRegion': active_region,
                'VPCId': active_vpc_id
            }
        )
    except route53_client.exceptions.ConflictingDomainExists:
        # VPC is already associated, which is fine
        pass
    except Exception as e:
        return {"error": f"Failed to associate VPC with hosted zone: {str(e)}"}

    # Get current VPC associations
    current_associations = zone_info["zone_info"].get("VPCs", [])

    # Disassociate other VPCs (except the active one)
    for vpc in current_associations:
        vpc_id = vpc.get("VPCId")
        vpc_region = vpc.get("VPCRegion")

        if vpc_id != active_vpc_id or vpc_region != active_region:
            try:
                route53_client.disassociate_vpc_from_hosted_zone(
                    HostedZoneId=zone_id,
                    VPC={
                        'VPCRegion': vpc_region,
                        'VPCId': vpc_id
                    }
                )
            except Exception as e:
                return {"error": f"Failed to disassociate VPC {vpc_id}: {str(e)}"}

    # Update DNS records for brokers
    broker_addresses = broker_info["broker_addresses"]

    changes = []
    for i, broker in enumerate(broker_addresses, 1):
        # Extract broker hostname (remove port)
        broker_host = broker.split(':')[0]

        # Create DNS record
        dns_name = f"broker{i}.{zone_name}"

        changes.append({
            'Action': 'UPSERT',
            'ResourceRecordSet': {
                'Name': dns_name,
                'Type': 'CNAME',
                'TTL': 300,
                'ResourceRecords': [
                    {'Value': broker_host}
                ]
            }
        })

    # Apply changes
    try:
        response = route53_client.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={'Changes': changes}
        )

        mode = "DR" if activate_dr else "Primary"
        return {
            "success": True,
            "message": f"Successfully switched to {mode} cluster {active_cluster_name} in {active_region}",
            "broker_dns_names": [f"broker{i}.{zone_name[:-1]}" for i in range(1, len(broker_addresses) + 1)],
            "change_info": response['ChangeInfo']
        }
    except Exception as e:
        return {"error": f"Failed to update DNS records: {str(e)}"}
