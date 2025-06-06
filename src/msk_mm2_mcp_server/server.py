#!/usr/bin/env python3
"""
MSK MirrorMaker2 Connector MCP Server - Optimized Version

This MCP (Model Context Protocol) server provides comprehensive tools to manage AWS MSK Connect 
connectors, plugins, and worker configurations specifically designed for MirrorMaker2 use cases.

The server enables LLMs to:
- Create and manage custom plugins for MirrorMaker2
- Set up complete MirrorMaker2 replication pipelines
- Monitor and troubleshoot connector status
- Configure authentication (IAM, PLAINTEXT, SCRAM-SHA-512)
- Manage worker configurations and capacity settings

Key Features:
- All async methods converted to sync for better reliability
- Comprehensive error handling with detailed error messages
- Status checking for long-running operations with timeouts
- Retry mechanisms with exponential backoff
- Resource state monitoring and validation
- Support for multiple authentication methods
- Complete MirrorMaker2 setup orchestration

Architecture:
- Resources: Expose read-only data about connectors, plugins, and configurations
- Tools: Provide actionable operations for creating and managing components
- Prompts: Offer guidance templates for complex setup scenarios
"""

import os
import json
import tempfile
import zipfile
import time
from typing import Dict, Any, List, Optional
import boto3
from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError
from botocore.config import Config

from mcp.server.fastmcp import FastMCP

# Configuration Constants
# These timeouts can be overridden via environment variables for different deployment scenarios
AWS_READ_TIMEOUT = int(os.getenv('AWS_READ_TIMEOUT', '300'))   # 5 minutes - for long-running operations
AWS_CONNECT_TIMEOUT = int(os.getenv('AWS_CONNECT_TIMEOUT', '60'))  # 1 minute - for initial connection

# Initialize the MCP server with descriptive name and dependencies
mcp = FastMCP("MSK MirrorMaker2 Connector", 
             dependencies=["boto3", "botocore"])

def get_aws_config():
    """
    Creates AWS client configuration optimized for MSK Connect operations.
    
    This configuration includes:
    - Extended timeouts for long-running operations (connector creation can take 10+ minutes)
    - Adaptive retry strategy to handle transient AWS API errors
    - Regional configuration with fallback to ap-southeast-1
    - Connection pooling for better performance
    
    Returns:
        Config: Boto3 configuration object with optimized settings
    """
    return Config(
        read_timeout=AWS_READ_TIMEOUT,
        connect_timeout=AWS_CONNECT_TIMEOUT,
        retries={
            'max_attempts': 3,
            'mode': 'adaptive'
        },
        #region_name=os.getenv('AWS_REGION', 'ap-southeast-1'),
        max_pool_connections=50
    )

def get_kafka_connect_client():
    """
    Creates and returns an MSK Connect client with proper error handling.
    
    This client is used for all MSK Connect operations including:
    - Creating and managing connectors
    - Managing custom plugins
    - Configuring worker settings
    
    Returns:
        boto3.client: Configured MSK Connect client
        
    Raises:
        Exception: If AWS credentials are not found or client creation fails
    """
    try:
        return boto3.client('kafkaconnect', config=get_aws_config())
    except NoCredentialsError:
        raise Exception("AWS credentials not found. Please configure AWS credentials using AWS CLI, environment variables, or IAM roles.")
    except Exception as e:
        raise Exception(f"Failed to create Kafka Connect client: {str(e)}")

def get_s3_client():
    """
    Creates and returns an S3 client for plugin file management.
    
    Used specifically for:
    - Uploading plugin ZIP files
    - Managing plugin artifacts in S3
    
    Returns:
        boto3.client: Configured S3 client
        
    Raises:
        Exception: If AWS credentials are not found or client creation fails
    """
    try:
        return boto3.client('s3', config=get_aws_config())
    except NoCredentialsError:
        raise Exception("AWS credentials not found. Please configure AWS credentials.")
    except Exception as e:
        raise Exception(f"Failed to create S3 client: {str(e)}")

def validate_required_params(params: Dict[str, Any], required_fields: List[str]) -> None:
    """
    Validates that all required parameters are provided and non-empty.
    
    This is crucial for MCP tools as LLMs may sometimes omit required parameters.
    Provides clear error messages to help LLMs understand what's missing.
    
    Args:
        params: Dictionary of provided parameters
        required_fields: List of parameter names that must be present
        
    Raises:
        ValueError: If any required parameters are missing or empty
    """
    missing_fields = [field for field in required_fields if not params.get(field)]
    if missing_fields:
        raise ValueError(f"Missing required parameters: {', '.join(missing_fields)}. Please provide all required parameters.")

def wait_for_resource_state(
    check_function,
    target_states: List[str],
    failure_states: List[str],
    max_wait_seconds: int = 600,
    check_interval: int = 15,
    resource_name: str = "resource"
) -> Dict[str, Any]:
    """
    Generic function to wait for AWS resource state changes with timeout handling.
    
    This is essential for MSK Connect operations as they are asynchronous and can take
    several minutes to complete. Provides consistent polling behavior across all resources.
    
    Args:
        check_function: Callable that returns the current state of the resource
        target_states: List of states that indicate successful completion
        failure_states: List of states that indicate operation failure
        max_wait_seconds: Maximum time to wait before timing out (default: 10 minutes)
        check_interval: Seconds to wait between status checks (default: 15 seconds)
        resource_name: Human-readable name of the resource for error messages
    
    Returns:
        Dict containing:
            - success (bool): Whether the resource reached a target state
            - state (str): Final state of the resource
            - message (str): Descriptive message about the outcome
    """
    start_time = time.time()
    
    while time.time() - start_time < max_wait_seconds:
        try:
            current_state = check_function()
            
            if current_state in target_states:
                return {
                    "success": True,
                    "state": current_state,
                    "message": f"{resource_name} successfully reached target state: {current_state}"
                }
            elif current_state in failure_states:
                return {
                    "success": False,
                    "state": current_state,
                    "message": f"{resource_name} failed with state: {current_state}"
                }
            
            time.sleep(check_interval)
            
        except Exception as e:
            return {
                "success": False,
                "state": "ERROR",
                "message": f"Error checking {resource_name} state: {str(e)}"
            }
    
    return {
        "success": False,
        "state": "TIMEOUT",
        "message": f"{resource_name} did not reach target state within {max_wait_seconds} seconds"
    }

def generate_cluster_auth_config(
    cluster_alias: str,
    auth_type: str,
    role_arn: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, str]:
    """
    Generates Kafka cluster authentication configuration for MirrorMaker2 connectors.
    
    This function creates the complex authentication configuration required for different
    Kafka security protocols. Each authentication type requires specific SASL and security
    configurations for producers, consumers, and the main cluster connection.
    
    Args:
        cluster_alias: Cluster identifier ('source' or 'target') used in configuration keys
        auth_type: Authentication method ('iam', 'plaintext', or 'scram')
        role_arn: IAM role ARN (required for IAM authentication)
        username: Username for SCRAM authentication
        password: Password for SCRAM authentication
    
    Returns:
        Dict[str, str]: Configuration dictionary with all necessary authentication parameters
        
    Raises:
        ValueError: If required parameters for the specified auth_type are missing
        
    Note:
        - IAM authentication requires an IAM role with proper MSK permissions
        - SCRAM authentication requires pre-configured user credentials in MSK
        - PLAINTEXT should only be used in development environments
    """
    config = {}
    
    if auth_type.lower() == 'iam':
        if not role_arn:
            raise ValueError(f"IAM role ARN is required for IAM authentication on {cluster_alias} cluster")
        
        # IAM authentication configuration for MSK
        # Uses AWS MSK IAM authentication plugin
        config.update({
            f"{cluster_alias}.cluster.security.protocol": "SASL_SSL",
            f"{cluster_alias}.cluster.producer.security.protocol": "SASL_SSL",
            f"{cluster_alias}.cluster.consumer.security.protocol": "SASL_SSL",
            f"{cluster_alias}.cluster.sasl.mechanism": "AWS_MSK_IAM",
            f"{cluster_alias}.cluster.producer.sasl.mechanism": "AWS_MSK_IAM",
            f"{cluster_alias}.cluster.consumer.sasl.mechanism": "AWS_MSK_IAM",
            f"{cluster_alias}.cluster.sasl.jaas.config": f'software.amazon.msk.auth.iam.IAMLoginModule required awsRoleArn="{role_arn}" awsDebugCreds=true;',
            f"{cluster_alias}.cluster.producer.sasl.jaas.config": f'software.amazon.msk.auth.iam.IAMLoginModule required awsRoleArn="{role_arn}" awsDebugCreds=true;',
            f"{cluster_alias}.cluster.consumer.sasl.jaas.config": f'software.amazon.msk.auth.iam.IAMLoginModule required awsRoleArn="{role_arn}" awsDebugCreds=true;',
            f"{cluster_alias}.cluster.sasl.client.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler",
            f"{cluster_alias}.cluster.producer.sasl.client.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler",
            f"{cluster_alias}.cluster.consumer.sasl.client.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler"
        })
    elif auth_type.lower() == 'plaintext':
        # Plaintext configuration - no encryption or authentication
        # WARNING: Only use in development environments
        config.update({
            f"{cluster_alias}.cluster.security.protocol": "PLAINTEXT",
            f"{cluster_alias}.cluster.producer.security.protocol": "PLAINTEXT",
            f"{cluster_alias}.cluster.consumer.security.protocol": "PLAINTEXT"
        })
    elif auth_type.lower() == 'scram':
        if not username or not password:
            raise ValueError(f"Username and password are required for SCRAM authentication on {cluster_alias} cluster")
        
        # SCRAM-SHA-512 authentication configuration
        # Requires pre-configured user credentials in MSK
        config.update({
            f"{cluster_alias}.cluster.security.protocol": "SASL_SSL",
            f"{cluster_alias}.cluster.producer.security.protocol": "SASL_SSL",
            f"{cluster_alias}.cluster.consumer.security.protocol": "SASL_SSL",
            f"{cluster_alias}.cluster.sasl.mechanism": "SCRAM-SHA-512",
            f"{cluster_alias}.cluster.producer.sasl.mechanism": "SCRAM-SHA-512",
            f"{cluster_alias}.cluster.consumer.sasl.mechanism": "SCRAM-SHA-512",
            f"{cluster_alias}.cluster.sasl.jaas.config": f'org.apache.kafka.common.security.scram.ScramLoginModule required username="{username}" password="{password}";',
            f"{cluster_alias}.cluster.producer.sasl.jaas.config": f'org.apache.kafka.common.security.scram.ScramLoginModule required username="{username}" password="{password}";',
            f"{cluster_alias}.cluster.consumer.sasl.jaas.config": f'org.apache.kafka.common.security.scram.ScramLoginModule required username="{username}" password="{password}";'
        })
    else:
        raise ValueError(f"Unsupported authentication type: {auth_type}. Supported types: iam, plaintext, scram")
    
    return config

# Resources - Expose read-only data for LLM consumption
# These resources allow LLMs to discover and inspect existing MSK Connect components

@mcp.resource("msk://connectors")
def list_all_connectors() -> List[Dict]:
    """
    Resource: List all MSK Connect connectors in the current AWS account/region.
    
    This resource provides a comprehensive view of all existing connectors, allowing
    LLMs to understand the current state of the MSK Connect environment before
    making changes or creating new connectors.
    
    Returns:
        List[Dict]: List of connector objects with basic information including:
            - connectorName: Name of the connector
            - connectorArn: AWS ARN of the connector
            - connectorState: Current state (CREATING, RUNNING, FAILED, etc.)
            - connectorDescription: Optional description
            - creationTime: When the connector was created
            - currentVersion: Current version of the connector
    """
    try:
        client = get_kafka_connect_client()
        response = client.list_connectors()
        return response.get('connectors', [])
    except Exception as e:
        return [{"error": f"Failed to list connectors: {str(e)}"}]

@mcp.resource("msk://connectors/{connector_name}")
def get_connector_details(connector_name: str) -> Dict:
    """
    Resource: Get detailed information about a specific MSK Connect connector.
    
    This resource provides comprehensive details about a connector including its
    configuration, status, and runtime information. Essential for troubleshooting
    and understanding connector behavior.
    
    Args:
        connector_name: Name of the connector to retrieve details for
        
    Returns:
        Dict: Detailed connector information including:
            - connectorName: Name of the connector
            - connectorArn: AWS ARN
            - connectorState: Current operational state
            - connectorConfiguration: All configuration parameters
            - kafkaConnectVersion: Version of Kafka Connect being used
            - capacity: Provisioned capacity (MCU count, worker count)
            - plugins: Custom plugins being used
            - serviceExecutionRoleArn: IAM role for the connector
    """
    try:
        client = get_kafka_connect_client()
        # First list all connectors to find the matching ARN
        connectors_response = client.list_connectors()
        connector_arn = None
        
        for connector in connectors_response.get('connectors', []):
            if connector['connectorName'] == connector_name:
                connector_arn = connector['connectorArn']
                break
        
        if not connector_arn:
            return {"error": f"Connector {connector_name} not found"}
        
        response = client.describe_connector(connectorArn=connector_arn)
        return response['connectorDescription']
    except Exception as e:
        return {"error": f"Failed to get connector details: {str(e)}"}

@mcp.resource("msk://plugins")
def list_all_plugins() -> List[Dict]:
    """
    Resource: List all MSK Connect custom plugins.
    
    Custom plugins contain the JAR files and dependencies needed for specific
    connector types. This resource helps LLMs understand what plugins are
    available for creating new connectors.
    
    Returns:
        List[Dict]: List of custom plugin objects including:
            - name: Plugin name
            - customPluginArn: AWS ARN of the plugin
            - customPluginState: Current state (ACTIVE, CREATING, CREATE_FAILED, etc.)
            - description: Optional description
            - creationTime: When the plugin was created
            - latestRevision: Current revision information
    """
    try:
        client = get_kafka_connect_client()
        response = client.list_custom_plugins()
        return response.get('customPlugins', [])
    except Exception as e:
        return [{"error": f"Failed to list plugins: {str(e)}"}]

@mcp.resource("msk://worker-configs")
def list_all_worker_configs() -> List[Dict]:
    """
    Resource: List all MSK Connect worker configurations.
    
    Worker configurations define the behavior of Kafka Connect workers, including
    converters, transforms, and other operational parameters. This resource helps
    LLMs understand available configurations for new connectors.
    
    Returns:
        List[Dict]: List of worker configuration objects including:
            - name: Configuration name
            - workerConfigurationArn: AWS ARN
            - workerConfigurationState: Current state
            - description: Optional description
            - creationTime: When the configuration was created
            - latestRevision: Current revision with properties
    """
    try:
        client = get_kafka_connect_client()
        response = client.list_worker_configurations()
        return response.get('workerConfigurations', [])
    except Exception as e:
        return {"error": f"Failed to list worker configurations: {str(e)}"}

@mcp.resource("msk://connector-status/{connector_name}")
def get_connector_status(connector_name: str) -> Dict:
    """
    Resource: Get quick status information for a specific connector.
    
    This resource provides a simplified view of connector status, useful for
    health checks and monitoring. Returns key operational metrics without
    the full configuration details.
    
    Args:
        connector_name: Name of the connector to check status for
        
    Returns:
        Dict: Status information including:
            - name: Connector name
            - state: Current operational state
            - version: Current version
            - creation_time: When the connector was created
            - kafka_connect_version: Kafka Connect version in use
    """
    try:
        connector_details = get_connector_details(connector_name)
        if "error" in connector_details:
            return connector_details
        
        return {
            "name": connector_details.get("connectorName"),
            "state": connector_details.get("connectorState"),
            "version": connector_details.get("currentVersion"),
            "creation_time": connector_details.get("creationTime"),
            "kafka_connect_version": connector_details.get("kafkaConnectVersion")
        }
    except Exception as e:
        return {"error": f"Failed to get connector status: {str(e)}"}

@mcp.prompt()
def setup_complete_mm2_replication(
    source_cluster_name: str,
    target_cluster_name: str
) -> str:
    """
    Prompt: Comprehensive guide for setting up complete MirrorMaker2 replication.
    
    This prompt provides step-by-step instructions for LLMs to understand the complete
    process of setting up MirrorMaker2 replication between two Kafka clusters.
    
    Args:
        source_cluster_name: Name of the source Kafka cluster
        target_cluster_name: Name of the target Kafka cluster
        
    Returns:
        str: Detailed setup guide with actionable steps
    """
    
    return f"""
# MirrorMaker2 Complete Replication Setup Guide

You are setting up MirrorMaker2 replication from **{source_cluster_name}** to **{target_cluster_name}**.

## Setup Steps:

### 1. Prerequisites
Ensure you have the following information:
- Source and target cluster bootstrap servers
- Authentication configuration (IAM roles, SCRAM credentials, etc.)
- VPC subnets and security groups information
- Service execution role ARN with proper permissions

### 2. Create Plugin
First create an empty plugin ZIP file:
```
create_empty_plugin_zip("your-bucket-name", "mm2/mm2-plugin.zip")
```

Then create the custom plugin:
```
create_custom_plugin("mm2-plugin", "your-bucket-name", "mm2/mm2-plugin.zip")
```

### 3. Create Worker Configuration (Optional)
If you need custom worker configuration:
```
create_worker_configuration("mm2-worker-config", {{
    "key.converter": "org.apache.kafka.connect.converters.ByteArrayConverter",
    "value.converter": "org.apache.kafka.connect.converters.ByteArrayConverter"
}})
```

### 4. Create Three Connectors
Create connectors in this specific order:

1. **MirrorHeartbeatConnector** - For cluster health monitoring
2. **MirrorCheckpointConnector** - For consumer offset synchronization  
3. **MirrorSourceConnector** - For topic data replication

### 5. Monitor and Validate
- Check connector status using check_connector_status
- Verify topic replication
- Monitor consumer lag
- Check CloudWatch logs for any errors

## Next Steps
Please provide the specific cluster information, and I will help you create the corresponding connectors.

## Important Notes:
- The order of connector creation is critical for proper operation
- Wait for each connector to reach RUNNING state before creating the next one
- Ensure proper IAM permissions are in place for cross-cluster access
"""

@mcp.prompt()
def troubleshoot_connector(connector_name: str) -> str:
    """
    Prompt: Comprehensive troubleshooting guide for MSK Connect connectors.
    
    This prompt provides systematic troubleshooting steps for LLMs to diagnose
    and resolve common connector issues. Covers network, authentication, 
    configuration, and operational problems.
    
    Args:
        connector_name: Name of the connector to troubleshoot
        
    Returns:
        str: Detailed troubleshooting checklist and diagnostic steps
    """
    
    return f"""
# Connector Troubleshooting Guide

For connector: **{connector_name}**

## Common Issues Checklist:

### 1. Basic Status Check
- [ ] Check connector status (RUNNING/FAILED/PAUSED)
- [ ] Review connector configuration for errors
- [ ] Verify plugin is in ACTIVE state

### 2. Network and Permissions
- [ ] Check VPC security groups allow Kafka ports (9092, 9094, 9096)
- [ ] Verify service execution role permissions
- [ ] Confirm subnet routing configuration
- [ ] Ensure NAT Gateway/Internet Gateway access if needed

### 3. Authentication Configuration
- [ ] IAM role configuration is correct
- [ ] SASL configuration matches cluster settings
- [ ] Certificate and encryption configuration
- [ ] Cross-account role assumptions (if applicable)

### 4. Kafka Clusters
- [ ] Source and target clusters are accessible
- [ ] Bootstrap servers addresses are correct
- [ ] Clusters are healthy and running
- [ ] Topic permissions are properly configured

### 5. Configuration Parameters
- [ ] Topic filter rules are correct
- [ ] Replication factor settings are reasonable
- [ ] Task count configuration is appropriate
- [ ] Consumer group configurations are valid

## Diagnostic Commands
Use these tools to gather detailed information:
- `describe_connector()` - View detailed configuration
- `check_connector_status()` - Check current status
- `wait_for_connector_ready()` - Monitor state changes
- Check CloudWatch logs for error details

## Common Solutions
1. **FAILED Status**: Check configuration and permissions
2. **PAUSED Status**: May be temporary, try restarting
3. **Connection Timeout**: Check network and security group configuration
4. **Authentication Failure**: Verify IAM roles and SASL configuration
5. **Topic Access Denied**: Check Kafka ACLs and IAM policies

## Advanced Troubleshooting
- Review connector task logs in CloudWatch
- Check MSK cluster metrics
- Validate network connectivity using VPC flow logs
- Test authentication separately using Kafka client tools
"""

@mcp.prompt()
def optimize_mm2_performance(replication_scale: str) -> str:
    """
    Prompt: Performance optimization recommendations for MirrorMaker2 deployments.
    
    This prompt provides comprehensive performance tuning guidance for LLMs to
    optimize MirrorMaker2 connectors based on different replication scales and
    throughput requirements.
    
    Args:
        replication_scale: Scale of replication (small, medium, large)
        
    Returns:
        str: Detailed performance optimization recommendations
    """
    
    return f"""
# MirrorMaker2 Performance Optimization Guide

For **{replication_scale}** scale replication scenarios

## Configuration Optimization Recommendations

### 1. Connector Level Configuration
```
tasks.max: Adjust based on topic partition count (recommended 1-2 tasks per partition)
consumer.max.poll.records: 500-1000
producer.batch.size: 16384-32768
producer.linger.ms: 5-100
producer.compression.type: snappy or lz4
buffer.memory: 67108864 (64MB)
```

### 2. Worker Configuration
```
offset.flush.interval.ms: 10000
offset.flush.timeout.ms: 5000
connector.client.config.override.policy: All
config.storage.replication.factor: 3
offset.storage.replication.factor: 3
status.storage.replication.factor: 3
```

### 3. Capacity Planning
- **Small Scale** (< 100 MB/s): 1 MCU, 1 Worker
- **Medium Scale** (100 MB/s - 1 GB/s): 2-4 MCU, 2-4 Workers  
- **Large Scale** (> 1 GB/s): 4+ MCU, 4+ Workers
- **Enterprise Scale** (> 5 GB/s): 8+ MCU, 8+ Workers

### 4. Monitoring Metrics
- Consumer lag per topic/partition
- Connector task status and health
- Throughput and latency metrics
- Error rate and retry counts
- Network utilization
- JVM heap usage

### 5. Topic Filtering Optimization
```
topics: Explicitly specify topics to replicate
topics.exclude: Exclude unnecessary internal topics
groups.exclude: Exclude temporary consumer groups
refresh.topics.interval.seconds: 300
```

### 6. Network and Infrastructure
- Use dedicated VPC for MSK Connect
- Optimize security group rules
- Consider placement groups for high throughput
- Monitor network bandwidth utilization

## Best Practices
1. Start Heartbeat first, then Checkpoint, finally Source connector
2. Use consistent cluster.alias configuration across all connectors
3. Regularly monitor and adjust parameters based on metrics
4. Consider network bandwidth and latency in planning
5. Implement proper monitoring and alerting
6. Use appropriate instance types for MSK Connect workers
7. Plan for disaster recovery and failover scenarios

## Performance Tuning by Scale

### Small Scale (< 100 MB/s)
- Single MCU sufficient
- Focus on reliability over throughput
- Monitor consumer lag closely

### Medium Scale (100 MB/s - 1 GB/s)
- Scale MCU based on partition count
- Optimize batch sizes and linger times
- Consider consumer group balancing

### Large Scale (> 1 GB/s)
- Multiple MCUs with proper distribution
- Optimize network configuration
- Implement comprehensive monitoring
- Consider topic sharding strategies
"""

# Core Tools - Primary operations for managing MSK Connect resources
# These tools provide the main functionality for creating and managing MirrorMaker2 components

@mcp.tool()
def create_empty_plugin_zip(bucket_name: str, key_path: str = "mm2/mm2.zip") -> str:
    """
    Tool: Create an empty ZIP file and upload it to S3 for MSK Connect plugin use.
    
    This tool creates a minimal valid ZIP file that can be used as a plugin for MSK Connect.
    Since MirrorMaker2 connectors use built-in Kafka Connect classes, an empty plugin
    is sufficient. This approach avoids the complexity of managing JAR files while
    satisfying MSK Connect's requirement for a custom plugin.
    
    Use Case:
    - First step in MirrorMaker2 setup
    - Creating a placeholder plugin for built-in connectors
    - Avoiding JAR file management complexity
    
    Args:
        bucket_name (str): Name of the S3 bucket where the ZIP file will be stored.
                          Must be accessible by the MSK Connect service role.
        key_path (str): S3 object key (path) for the ZIP file. 
                       Defaults to "mm2/mm2.zip" for organization.
    
    Returns:
        str: Success message with S3 URL or error details if the operation fails.
             Format: "Successfully created and uploaded empty plugin ZIP to: s3://bucket/path"
    
    Prerequisites:
        - S3 bucket must exist and be accessible
        - AWS credentials must have s3:PutObject permission on the bucket
        - Bucket should be in the same region as MSK Connect for optimal performance
    
    Example:
        create_empty_plugin_zip("my-kafka-plugins", "mirrormaker2/plugin.zip")
    """
    try:
        validate_required_params(
            {"bucket_name": bucket_name, "key_path": key_path},
            ["bucket_name", "key_path"]
        )
        
        s3_client = get_s3_client()
        
        # Create a temporary zip file with an empty directory structure
        # This creates a valid ZIP file that satisfies MSK Connect requirements
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_file:
            with zipfile.ZipFile(temp_file.name, 'w') as zipf:
                # Add an empty directory to make it a valid, non-empty zip
                # The mm2/ directory serves as a namespace for the plugin
                zipf.writestr('mm2/', '')
            
            # Upload the ZIP file to S3
            s3_client.upload_file(temp_file.name, bucket_name, key_path)
            
            # Clean up the temporary file to avoid disk space issues
            os.unlink(temp_file.name)
        
        s3_url = f"s3://{bucket_name}/{key_path}"
        return f"Successfully created and uploaded empty plugin ZIP to: {s3_url}"
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Upload failed ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to create ZIP file: {str(e)}"

@mcp.tool()
def create_custom_plugin(
    plugin_name: str,
    s3_bucket: str,
    s3_key: str,
    description: str = "MirrorMaker2 plugin for MSK Connect"
) -> str:
    """
    Tool: Create an MSK Connect custom plugin from an S3-stored ZIP file.
    
    This tool registers a custom plugin with MSK Connect, making it available for
    connector creation. The plugin creation is asynchronous and may take several
    minutes to complete. Use check_plugin_status or wait_for_plugin_ready to
    monitor the plugin state before creating connectors.
    
    Use Case:
    - Register plugin ZIP files with MSK Connect
    - Second step in MirrorMaker2 setup (after uploading ZIP to S3)
    - Making custom connector classes available for use
    
    Args:
        plugin_name (str): Unique name for the plugin within the AWS account/region.
                          Must be unique and follow AWS naming conventions.
        s3_bucket (str): Name of the S3 bucket containing the plugin ZIP file.
                        Must be accessible by the MSK Connect service.
        s3_key (str): S3 object key (path) of the ZIP file containing the plugin.
                     Should point to the ZIP file created by create_empty_plugin_zip.
        description (str): Human-readable description of the plugin's purpose.
                          Defaults to "MirrorMaker2 plugin for MSK Connect".
    
    Returns:
        str: Creation status message including plugin ARN and initial state.
             Format: "Plugin creation started: [name], ARN: [arn], Initial state: [state]"
    
    Prerequisites:
        - S3 bucket and object must exist and be accessible
        - MSK Connect service role must have s3:GetObject permission on the bucket
        - Plugin name must be unique within the AWS account/region
    
    Next Steps:
        - Use check_plugin_status(plugin_arn) to monitor creation progress
        - Wait for plugin to reach ACTIVE state before creating connectors
        - Plugin ARN from response is needed for connector creation
    
    Example:
        create_custom_plugin("my-mm2-plugin", "kafka-plugins-bucket", "mm2/plugin.zip")
    """
    try:
        validate_required_params(
            {"plugin_name": plugin_name, "s3_bucket": s3_bucket, "s3_key": s3_key},
            ["plugin_name", "s3_bucket", "s3_key"]
        )
        
        client = get_kafka_connect_client()
        
        # Create the custom plugin by registering the S3 location
        # MSK Connect will download and validate the ZIP file
        response = client.create_custom_plugin(
            name=plugin_name,
            contentType='ZIP',  # Only ZIP format is supported
            location={
                's3Location': {
                    'bucketArn': f'arn:aws:s3:::{s3_bucket}',
                    'fileKey': s3_key
                }
            },
            description=description
        )
        
        plugin_arn = response['customPluginArn']
        plugin_state = response['customPluginState']
        
        return f"Plugin creation started: {response['customPluginName']}, ARN: {plugin_arn}, Initial state: {plugin_state}. Use check_plugin_status to monitor final status."
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Plugin creation failed ({error_code}): {str(e)}"
    except Exception as e:
        return f"Plugin creation failed: {str(e)}"

@mcp.tool()
def check_plugin_status(plugin_arn: str) -> str:
    """
    Tool: Check the current status of an MSK Connect custom plugin.
    
    This tool provides real-time status information for a custom plugin,
    including its current state and creation details. Essential for monitoring
    plugin creation progress and troubleshooting issues.
    
    Use Case:
    - Monitor plugin creation progress after create_custom_plugin
    - Troubleshoot plugin creation failures
    - Verify plugin readiness before creating connectors
    - Check plugin health during operations
    
    Args:
        plugin_arn (str): AWS ARN of the custom plugin to check.
                         Obtained from create_custom_plugin response.
    
    Returns:
        str: Detailed status information including:
             - Plugin name and ARN
             - Current state (ACTIVE, CREATING, CREATE_FAILED, DELETING)
             - Creation timestamp
             - Status interpretation and next steps
    
    Plugin States:
        - CREATING: Plugin is being created (normal during setup)
        - ACTIVE: Plugin is ready for use in connectors
        - CREATE_FAILED: Plugin creation failed (check S3 permissions/content)
        - DELETING: Plugin is being deleted
    
    Example:
        check_plugin_status("arn:aws:kafkaconnect:region:account:custom-plugin/name/uuid")
    """
    try:
        validate_required_params({"plugin_arn": plugin_arn}, ["plugin_arn"])
        
        client = get_kafka_connect_client()
        response = client.describe_custom_plugin(customPluginArn=plugin_arn)
        
        plugin_state = response['customPluginState']
        plugin_name = response['name']
        
        status_info = f"Plugin: {plugin_name}\n"
        status_info += f"ARN: {plugin_arn}\n"
        status_info += f"State: {plugin_state}\n"
        status_info += f"Creation Time: {response.get('creationTime', 'N/A')}\n"
        
        # Provide actionable status interpretation
        if plugin_state == 'ACTIVE':
            status_info += "✅ Plugin is ready and can be used to create connectors"
        elif plugin_state == 'CREATING':
            status_info += "⏳ Plugin is being created, please check again in a few minutes"
        elif plugin_state == 'CREATE_FAILED':
            status_info += "❌ Plugin creation failed - check S3 permissions and ZIP file content"
        elif plugin_state == 'DELETING':
            status_info += "🗑️ Plugin is being deleted"
        
        return status_info
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to check plugin status ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to check plugin status: {str(e)}"

@mcp.tool()
def wait_for_plugin_ready(plugin_arn: str, max_wait_minutes: int = 10) -> str:
    """
    Tool: Wait for plugin to reach ACTIVE state with automatic status polling.
    
    This tool continuously monitors a plugin's status until it reaches the ACTIVE
    state or fails. Useful for automated workflows where you need to wait for
    plugin readiness before proceeding with connector creation.
    
    Use Case:
    - Automated MirrorMaker2 setup workflows
    - Ensuring plugin readiness before connector creation
    - Avoiding manual status checking in scripts
    - Error detection during plugin creation
    
    Args:
        plugin_arn (str): AWS ARN of the custom plugin to monitor.
        max_wait_minutes (int): Maximum time to wait in minutes before timing out.
                               Defaults to 10 minutes (typical plugin creation time).
    
    Returns:
        str: Final status message indicating success or failure.
             Success: "✅ Plugin successfully reached target state: ACTIVE"
             Failure: "❌ Plugin failed with state: [state]" or timeout message
    
    Polling Behavior:
        - Checks plugin status every 15 seconds
        - Returns immediately on ACTIVE state (success)
        - Returns immediately on CREATE_FAILED or DELETING (failure)
        - Times out after max_wait_minutes with informative message
    
    Example:
        wait_for_plugin_ready("arn:aws:kafkaconnect:region:account:custom-plugin/name/uuid", 15)
    """
    try:
        validate_required_params({"plugin_arn": plugin_arn}, ["plugin_arn"])
        
        client = get_kafka_connect_client()
        
        def check_plugin_state():
            response = client.describe_custom_plugin(customPluginArn=plugin_arn)
            return response['customPluginState']
        
        result = wait_for_resource_state(
            check_function=check_plugin_state,
            target_states=['ACTIVE'],
            failure_states=['CREATE_FAILED', 'DELETING'],
            max_wait_seconds=max_wait_minutes * 60,
            check_interval=15,
            resource_name="Plugin"
        )
        
        if result['success']:
            return f"✅ {result['message']}"
        else:
            return f"❌ {result['message']}"
    
    except Exception as e:
        return f"Failed to wait for plugin status: {str(e)}"

@mcp.tool()
def create_worker_configuration(
    worker_config_name: str,
    properties: Dict[str, str],
    description: str = "Worker configuration for MirrorMaker2"
) -> str:
    """
    Tool: Create an MSK Connect worker configuration with custom properties.
    
    Worker configurations define global settings for Kafka Connect workers, including
    converters, transforms, and operational parameters. These configurations can be
    reused across multiple connectors to ensure consistent behavior.
    
    Use Case:
    - Define custom converters (ByteArray, JSON, Avro)
    - Configure transforms and error handling
    - Set operational parameters like timeouts and retries
    - Standardize connector behavior across deployments
    
    Args:
        worker_config_name (str): Unique name for the worker configuration.
                                 Must be unique within the AWS account/region.
        properties (Dict[str, str]): Key-value pairs of worker configuration properties.
                                    Common properties include:
                                    - key.converter: Converter for message keys
                                    - value.converter: Converter for message values
                                    - connector.client.config.override.policy: Client override policy
        description (str): Human-readable description of the configuration's purpose.
                          Defaults to "Worker configuration for MirrorMaker2".
    
    Returns:
        str: Creation result with worker configuration name and ARN.
             Format: "Successfully created worker config: [name], ARN: [arn]"
    
    Common Properties:
        - key.converter: "org.apache.kafka.connect.converters.ByteArrayConverter"
        - value.converter: "org.apache.kafka.connect.converters.ByteArrayConverter"
        - connector.client.config.override.policy: "All"
        - offset.flush.interval.ms: "10000"
        - offset.flush.timeout.ms: "5000"
    
    Example:
        create_worker_configuration("mm2-worker-config", {
            "key.converter": "org.apache.kafka.connect.converters.ByteArrayConverter",
            "value.converter": "org.apache.kafka.connect.converters.ByteArrayConverter"
        })
    """
    try:
        validate_required_params(
            {"worker_config_name": worker_config_name, "properties": properties},
            ["worker_config_name", "properties"]
        )
        
        if not isinstance(properties, dict):
            return "Error: properties must be a dictionary format"
        
        client = get_kafka_connect_client()
        
        # Convert properties dict to the properties file format expected by the API
        # Each property becomes a "key=value" line
        properties_string = '\n'.join([f'{k}={v}' for k, v in properties.items()])
        
        response = client.create_worker_configuration(
            name=worker_config_name,
            description=description,
            propertiesFileContent=properties_string
        )
        
        return f"Successfully created worker configuration: {response['name']}, ARN: {response['workerConfigurationArn']}"
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Worker configuration creation failed ({error_code}): {str(e)}"
    except Exception as e:
        return f"Worker configuration creation failed: {str(e)}"

# Connector Creation Functions

@mcp.tool()
def create_mirror_heartbeat_connector(
    connector_name: str,
    source_bootstrap_servers: str,
    target_bootstrap_servers: str,
    plugin_arn: str,
    service_execution_role_arn: str,
    vpc_subnets: List[str],
    vpc_security_groups: List[str],
    source_auth_type: str = "iam",
    target_auth_type: str = "iam",
    source_role_arn: Optional[str] = None,
    target_role_arn: Optional[str] = None,
    source_username: Optional[str] = None,
    source_password: Optional[str] = None,
    target_username: Optional[str] = None,
    target_password: Optional[str] = None,
    replication_factor: int = 3,
    tasks_max: int = 1,
    kafka_connect_version: str = "2.7.1",
    worker_config_arn: Optional[str] = None
) -> str:
    """
    Tool: Create a MirrorHeartbeatConnector for cluster health monitoring.
    
    The MirrorHeartbeatConnector is the first component of a MirrorMaker2 setup.
    It sends periodic heartbeat messages between clusters to monitor connectivity
    and health. This connector should be created first in a MirrorMaker2 deployment.
    
    Use Case:
    - Monitor connectivity between source and target clusters
    - Detect network or authentication issues early
    - First step in a complete MirrorMaker2 setup
    - Health check mechanism for replication pipeline
    
    Args:
        connector_name (str): Unique name for the heartbeat connector
        source_bootstrap_servers (str): Comma-separated list of source cluster bootstrap servers
        target_bootstrap_servers (str): Comma-separated list of target cluster bootstrap servers
        plugin_arn (str): ARN of the custom plugin (from create_custom_plugin)
        service_execution_role_arn (str): IAM role ARN for MSK Connect service
        vpc_subnets (List[str]): List of VPC subnet IDs for connector deployment
        vpc_security_groups (List[str]): List of security group IDs for network access
        source_auth_type (str): Authentication type for source cluster (iam/plaintext/scram)
        target_auth_type (str): Authentication type for target cluster (iam/plaintext/scram)
        source_role_arn (Optional[str]): IAM role ARN for source cluster (if using IAM auth)
        target_role_arn (Optional[str]): IAM role ARN for target cluster (if using IAM auth)
        source_username (Optional[str]): Username for source cluster (if using SCRAM auth)
        source_password (Optional[str]): Password for source cluster (if using SCRAM auth)
        target_username (Optional[str]): Username for target cluster (if using SCRAM auth)
        target_password (Optional[str]): Password for target cluster (if using SCRAM auth)
        replication_factor (int): Replication factor for heartbeat topics (default: 3)
        tasks_max (int): Maximum number of tasks for the connector (default: 1)
        kafka_connect_version (str): Kafka Connect version to use (default: "2.7.1")
        worker_config_arn (Optional[str]): ARN of worker configuration (optional)
    
    Returns:
        str: Creation result with connector details and next steps
    
    Prerequisites:
        - Plugin must be in ACTIVE state
        - VPC subnets must have proper routing to both Kafka clusters
        - Security groups must allow Kafka ports (9092, 9094, 9096)
        - Service execution role must have necessary permissions
        - Authentication credentials must be valid for both clusters
    
    Next Steps:
        - Use check_connector_status to monitor creation progress
        - Wait for RUNNING state before creating MirrorCheckpointConnector
        - Verify heartbeat topic creation in target cluster
    """
    try:
        # Validate required parameters
        required_params = {
            "connector_name": connector_name,
            "source_bootstrap_servers": source_bootstrap_servers,
            "target_bootstrap_servers": target_bootstrap_servers,
            "plugin_arn": plugin_arn,
            "service_execution_role_arn": service_execution_role_arn,
            "vpc_subnets": vpc_subnets,
            "vpc_security_groups": vpc_security_groups
        }
        validate_required_params(required_params, list(required_params.keys()))
        
        if not vpc_subnets or not vpc_security_groups:
            return "Error: VPC subnets and security groups cannot be empty"
        
        client = get_kafka_connect_client()
        
        # Prepare connector configuration
        connector_config = {
            "connector.class": "org.apache.kafka.connect.mirror.MirrorHeartbeatConnector",
            "source.cluster.alias": "source",
            "target.cluster.alias": "target",
            "clusters": "source,target",
            "source.cluster.bootstrap.servers": source_bootstrap_servers,
            "target.cluster.bootstrap.servers": target_bootstrap_servers,
            
            # Other configurations
            "consumer.auto.offset.reset": "earliest",
            "producer.linger.ms": "500",
            "producer.retry.backoff.ms": "1000",
            "producer.max.block.ms": "10000",
            "replication.factor": str(replication_factor),
            "tasks.max": str(tasks_max),
            "key.converter": "org.apache.kafka.connect.converters.ByteArrayConverter",
            "value.converter": "org.apache.kafka.connect.converters.ByteArrayConverter"
        }
        
        # Add source cluster authentication configuration
        try:
            source_auth_config = generate_cluster_auth_config(
                "source", source_auth_type, source_role_arn, source_username, source_password
            )
            connector_config.update(source_auth_config)
        except ValueError as e:
            return f"Source cluster authentication configuration error: {str(e)}"
        
        # Add target cluster authentication configuration
        try:
            target_auth_config = generate_cluster_auth_config(
                "target", target_auth_type, target_role_arn, target_username, target_password
            )
            connector_config.update(target_auth_config)
        except ValueError as e:
            return f"Target cluster authentication configuration error: {str(e)}"
        
        # Prepare request parameters
        request_params = {
            "connectorName": connector_name,
            "kafkaConnectVersion": kafka_connect_version,
            "capacity": {
                "provisionedCapacity": {
                    "mcuCount": 1,
                    "workerCount": 1
                }
            },
            "connectorConfiguration": connector_config,
            "kafkaCluster": {
                "apacheKafkaCluster": {
                    "bootstrapServers": target_bootstrap_servers,
                    "vpc": {
                        "subnets": vpc_subnets,
                        "securityGroups": vpc_security_groups
                    }
                }
            },
            "kafkaClusterClientAuthentication": {
                "authenticationType": "IAM"
            },
            "kafkaClusterEncryptionInTransit": {
                "encryptionType": "TLS"
            },
            "plugins": [
                {
                    "customPlugin": {
                        "customPluginArn": plugin_arn,
                        "revision": 1
                    }
                }
            ],
            "serviceExecutionRoleArn": service_execution_role_arn
        }
        
        # Add worker configuration if provided
        if worker_config_arn:
            request_params["workerConfiguration"] = {
                "workerConfigurationArn": worker_config_arn,
                "revision": 1
            }
        
        response = client.create_connector(**request_params)
        
        return f"Successfully created MirrorHeartbeatConnector: {response['connectorName']}, ARN: {response['connectorArn']}, State: {response['connectorState']}. Use check_connector_status to monitor final status."
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"MirrorHeartbeatConnector creation failed ({error_code}): {str(e)}"
    except Exception as e:
        return f"MirrorHeartbeatConnector creation failed: {str(e)}"

@mcp.tool()
def create_mirror_checkpoint_connector(
    connector_name: str,
    source_bootstrap_servers: str,
    target_bootstrap_servers: str,
    plugin_arn: str,
    service_execution_role_arn: str,
    vpc_subnets: List[str],
    vpc_security_groups: List[str],
    source_auth_type: str = "iam",
    target_auth_type: str = "iam",
    source_role_arn: Optional[str] = None,
    target_role_arn: Optional[str] = None,
    source_username: Optional[str] = None,
    source_password: Optional[str] = None,
    target_username: Optional[str] = None,
    target_password: Optional[str] = None,
    replication_factor: int = 3,
    tasks_max: int = 1,
    kafka_connect_version: str = "2.7.1",
    worker_config_arn: Optional[str] = None
) -> str:
    """
    Tool: Create a MirrorCheckpointConnector for consumer offset synchronization.
    
    The MirrorCheckpointConnector is the second component of a MirrorMaker2 setup.
    It synchronizes consumer group offsets between source and target clusters,
    enabling consumers to resume from the correct position after failover.
    This connector should be created after the MirrorHeartbeatConnector.
    
    Use Case:
    - Synchronize consumer group offsets between clusters
    - Enable seamless consumer failover scenarios
    - Second step in a complete MirrorMaker2 setup
    - Maintain consumer position consistency across clusters
    
    Args:
        connector_name (str): Unique name for the checkpoint connector
        source_bootstrap_servers (str): Comma-separated list of source cluster bootstrap servers
        target_bootstrap_servers (str): Comma-separated list of target cluster bootstrap servers
        plugin_arn (str): ARN of the custom plugin (from create_custom_plugin)
        service_execution_role_arn (str): IAM role ARN for MSK Connect service
        vpc_subnets (List[str]): List of VPC subnet IDs for connector deployment
        vpc_security_groups (List[str]): List of security group IDs for network access
        source_auth_type (str): Authentication type for source cluster (iam/plaintext/scram)
        target_auth_type (str): Authentication type for target cluster (iam/plaintext/scram)
        source_role_arn (Optional[str]): IAM role ARN for source cluster (if using IAM auth)
        target_role_arn (Optional[str]): IAM role ARN for target cluster (if using IAM auth)
        source_username (Optional[str]): Username for source cluster (if using SCRAM auth)
        source_password (Optional[str]): Password for source cluster (if using SCRAM auth)
        target_username (Optional[str]): Username for target cluster (if using SCRAM auth)
        target_password (Optional[str]): Password for target cluster (if using SCRAM auth)
        replication_factor (int): Replication factor for checkpoint topics (default: 3)
        tasks_max (int): Maximum number of tasks for the connector (default: 1)
        kafka_connect_version (str): Kafka Connect version to use (default: "2.7.1")
        worker_config_arn (Optional[str]): ARN of worker configuration (optional)
    
    Returns:
        str: Creation result with connector details and monitoring instructions
    
    Prerequisites:
        - MirrorHeartbeatConnector should be running first
        - Plugin must be in ACTIVE state
        - VPC and security group configuration must allow cluster access
        - Authentication credentials must be valid for both clusters
    
    Next Steps:
        - Monitor connector status until RUNNING state
        - Verify checkpoint topic creation in target cluster
        - Create MirrorSourceConnector for data replication
    """
    try:
        # Validate required parameters
        required_params = {
            "connector_name": connector_name,
            "source_bootstrap_servers": source_bootstrap_servers,
            "target_bootstrap_servers": target_bootstrap_servers,
            "plugin_arn": plugin_arn,
            "service_execution_role_arn": service_execution_role_arn,
            "vpc_subnets": vpc_subnets,
            "vpc_security_groups": vpc_security_groups
        }
        validate_required_params(required_params, list(required_params.keys()))
        
        if not vpc_subnets or not vpc_security_groups:
            return "Error: VPC subnets and security groups cannot be empty"
        
        client = get_kafka_connect_client()
        
        # Prepare connector configuration
        connector_config = {
            "connector.class": "org.apache.kafka.connect.mirror.MirrorCheckpointConnector",
            "source.cluster.alias": "source",
            "target.cluster.alias": "target",
            "clusters": "source,target",
            "source.cluster.bootstrap.servers": source_bootstrap_servers,
            "target.cluster.bootstrap.servers": target_bootstrap_servers,
            
            # Checkpoint specific configurations
            "topics": ".*",
            "topics.exclude": ".*[-.]internal, .*.replica, __.*, .*-config, .*-status, .*-offset",
            "groups.exclude": "console-consumer-.*, connect-.*, __.*",
            "refresh.groups.enabled": "true",
            "refresh.groups.interval.seconds": "60",
            "emit.checkpoints.enabled": "true",
            "consumer.auto.offset.reset": "earliest",
            "producer.linger.ms": "500",
            "producer.retry.backoff.ms": "1000",
            "producer.max.block.ms": "10000",
            "replication.factor": str(replication_factor),
            "tasks.max": str(tasks_max),
            "key.converter": "org.apache.kafka.connect.converters.ByteArrayConverter",
            "value.converter": "org.apache.kafka.connect.converters.ByteArrayConverter",
            "sync.group.offsets.interval.seconds": "5"
        }
        
        # Add authentication configurations
        try:
            source_auth_config = generate_cluster_auth_config(
                "source", source_auth_type, source_role_arn, source_username, source_password
            )
            connector_config.update(source_auth_config)
            
            target_auth_config = generate_cluster_auth_config(
                "target", target_auth_type, target_role_arn, target_username, target_password
            )
            connector_config.update(target_auth_config)
        except ValueError as e:
            return f"Authentication configuration error: {str(e)}"
        
        # Prepare request parameters
        request_params = {
            "connectorName": connector_name,
            "kafkaConnectVersion": kafka_connect_version,
            "capacity": {
                "provisionedCapacity": {
                    "mcuCount": 1,
                    "workerCount": 1
                }
            },
            "connectorConfiguration": connector_config,
            "kafkaCluster": {
                "apacheKafkaCluster": {
                    "bootstrapServers": target_bootstrap_servers,
                    "vpc": {
                        "subnets": vpc_subnets,
                        "securityGroups": vpc_security_groups
                    }
                }
            },
            "kafkaClusterClientAuthentication": {
                "authenticationType": "IAM"
            },
            "kafkaClusterEncryptionInTransit": {
                "encryptionType": "TLS"
            },
            "plugins": [
                {
                    "customPlugin": {
                        "customPluginArn": plugin_arn,
                        "revision": 1
                    }
                }
            ],
            "serviceExecutionRoleArn": service_execution_role_arn
        }
        
        # Add worker configuration if provided
        if worker_config_arn:
            request_params["workerConfiguration"] = {
                "workerConfigurationArn": worker_config_arn,
                "revision": 1
            }
        
        response = client.create_connector(**request_params)
        
        return f"Successfully created MirrorCheckpointConnector: {response['connectorName']}, ARN: {response['connectorArn']}, State: {response['connectorState']}. Use check_connector_status to monitor final status."
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"MirrorCheckpointConnector creation failed ({error_code}): {str(e)}"
    except Exception as e:
        return f"MirrorCheckpointConnector creation failed: {str(e)}"

@mcp.tool()
def create_mirror_source_connector(
    connector_name: str,
    source_bootstrap_servers: str,
    target_bootstrap_servers: str,
    plugin_arn: str,
    service_execution_role_arn: str,
    vpc_subnets: List[str],
    vpc_security_groups: List[str],
    source_auth_type: str = "iam",
    target_auth_type: str = "iam",
    source_role_arn: Optional[str] = None,
    target_role_arn: Optional[str] = None,
    source_username: Optional[str] = None,
    source_password: Optional[str] = None,
    target_username: Optional[str] = None,
    target_password: Optional[str] = None,
    replication_factor: int = 3,
    tasks_max: int = 2,
    kafka_connect_version: str = "3.7.x",
    worker_config_arn: Optional[str] = None
) -> str:
    """
    Tool: Create a MirrorSourceConnector for topic data replication.
    
    The MirrorSourceConnector is the final component of a MirrorMaker2 setup.
    It replicates topic data and metadata from source to target cluster, creating
    topics with a cluster alias prefix in the target cluster. This connector
    should be created last after both heartbeat and checkpoint connectors are running.
    
    Use Case:
    - Replicate topic data from source to target cluster
    - Maintain topic metadata and configurations
    - Final step in a complete MirrorMaker2 setup
    - Enable cross-cluster data streaming and backup
    
    Args:
        connector_name (str): Unique name for the source connector
        source_bootstrap_servers (str): Comma-separated list of source cluster bootstrap servers
        target_bootstrap_servers (str): Comma-separated list of target cluster bootstrap servers
        plugin_arn (str): ARN of the custom plugin (from create_custom_plugin)
        service_execution_role_arn (str): IAM role ARN for MSK Connect service
        vpc_subnets (List[str]): List of VPC subnet IDs for connector deployment
        vpc_security_groups (List[str]): List of security group IDs for network access
        source_auth_type (str): Authentication type for source cluster (iam/plaintext/scram)
        target_auth_type (str): Authentication type for target cluster (iam/plaintext/scram)
        source_role_arn (Optional[str]): IAM role ARN for source cluster (if using IAM auth)
        target_role_arn (Optional[str]): IAM role ARN for target cluster (if using IAM auth)
        source_username (Optional[str]): Username for source cluster (if using SCRAM auth)
        source_password (Optional[str]): Password for source cluster (if using SCRAM auth)
        target_username (Optional[str]): Username for target cluster (if using SCRAM auth)
        target_password (Optional[str]): Password for target cluster (if using SCRAM auth)
        replication_factor (int): Replication factor for replicated topics (default: 3)
        tasks_max (int): Maximum number of tasks for the connector (default: 2)
        kafka_connect_version (str): Kafka Connect version to use (default: "3.7.x")
        worker_config_arn (Optional[str]): ARN of worker configuration (optional)
    
    Returns:
        str: Creation result with connector details and verification instructions
    
    Prerequisites:
        - MirrorHeartbeatConnector and MirrorCheckpointConnector should be running
        - Plugin must be in ACTIVE state
        - VPC and security group configuration must allow cluster access
        - Authentication credentials must be valid for both clusters
        - Sufficient permissions for topic creation in target cluster
    
    Next Steps:
        - Monitor connector status until RUNNING state
        - Verify topic replication in target cluster (topics prefixed with source cluster alias)
        - Check consumer lag and replication metrics
        - Validate data integrity between source and target topics
    """
    try:
        # Validate required parameters
        required_params = {
            "connector_name": connector_name,
            "source_bootstrap_servers": source_bootstrap_servers,
            "target_bootstrap_servers": target_bootstrap_servers,
            "plugin_arn": plugin_arn,
            "service_execution_role_arn": service_execution_role_arn,
            "vpc_subnets": vpc_subnets,
            "vpc_security_groups": vpc_security_groups
        }
        validate_required_params(required_params, list(required_params.keys()))
        
        if not vpc_subnets or not vpc_security_groups:
            return "Error: VPC subnets and security groups cannot be empty"
        
        client = get_kafka_connect_client()
        
        # Prepare connector configuration
        connector_config = {
            "connector.class": "org.apache.kafka.connect.mirror.MirrorSourceConnector",
            "tasks.max": str(tasks_max),
            "clusters": "source,target",
            "source.cluster.alias": "source",
            "target.cluster.alias": "target",
            "source.cluster.bootstrap.servers": source_bootstrap_servers,
            "target.cluster.bootstrap.servers": target_bootstrap_servers,
            
            # Source connector specific configurations
            "refresh.groups.enabled": "true",
            "refresh.groups.interval.seconds": "60",
            "refresh.topics.interval.seconds": "60",
            "topics.exclude": ".*[-.]internal,.*.replica,__.*,.*-config,.*-status,.*-offset",
            "emit.checkpoints.enabled": "true",
            "topics": ".*",
            "value.converter": "org.apache.kafka.connect.converters.ByteArrayConverter",
            "key.converter": "org.apache.kafka.connect.converters.ByteArrayConverter",
            "producer.max.block.ms": "10000",
            "producer.linger.ms": "500",
            "producer.retry.backoff.ms": "1000",
            "sync.topic.configs.enabled": "true",
            "sync.topic.configs.interval.seconds": "60",
            "refresh.topics.enabled": "true",
            "groups.exclude": "console-consumer-.*,connect-.*,__.*",
            "consumer.auto.offset.reset": "earliest",
            "replication.factor": str(replication_factor)
        }
        
        # Add authentication configurations
        try:
            source_auth_config = generate_cluster_auth_config(
                "source", source_auth_type, source_role_arn, source_username, source_password
            )
            connector_config.update(source_auth_config)
            
            target_auth_config = generate_cluster_auth_config(
                "target", target_auth_type, target_role_arn, target_username, target_password
            )
            connector_config.update(target_auth_config)
        except ValueError as e:
            return f"Authentication configuration error: {str(e)}"
        
        # Prepare request parameters
        request_params = {
            "connectorName": connector_name,
            "kafkaConnectVersion": kafka_connect_version,
            "capacity": {
                "provisionedCapacity": {
                    "mcuCount": 1,
                    "workerCount": 1
                }
            },
            "connectorConfiguration": connector_config,
            "kafkaCluster": {
                "apacheKafkaCluster": {
                    "bootstrapServers": target_bootstrap_servers,
                    "vpc": {
                        "subnets": vpc_subnets,
                        "securityGroups": vpc_security_groups
                    }
                }
            },
            "kafkaClusterClientAuthentication": {
                "authenticationType": "IAM"
            },
            "kafkaClusterEncryptionInTransit": {
                "encryptionType": "TLS"
            },
            "plugins": [
                {
                    "customPlugin": {
                        "customPluginArn": plugin_arn,
                        "revision": 1
                    }
                }
            ],
            "serviceExecutionRoleArn": service_execution_role_arn
        }
        
        # Add worker configuration if provided
        if worker_config_arn:
            request_params["workerConfiguration"] = {
                "workerConfigurationArn": worker_config_arn,
                "revision": 1
            }
        
        response = client.create_connector(**request_params)
        
        return f"Successfully created MirrorSourceConnector: {response['connectorName']}, ARN: {response['connectorArn']}, State: {response['connectorState']}. Use check_connector_status to monitor final status."
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"MirrorSourceConnector creation failed ({error_code}): {str(e)}"
    except Exception as e:
        return f"MirrorSourceConnector creation failed: {str(e)}"

# Management and Monitoring Functions

@mcp.tool()
def check_connector_status(connector_arn: str) -> str:
    """
    Tool: Check the current status of an MSK Connect connector.
    
    This tool provides real-time status information for a connector, including
    its operational state, version, and runtime details. Essential for monitoring
    connector health and troubleshooting issues.
    
    Use Case:
    - Monitor connector creation progress
    - Check operational health of running connectors
    - Troubleshoot connector failures
    - Verify connector state before performing operations
    
    Args:
        connector_arn (str): AWS ARN of the connector to check status for.
                            Obtained from connector creation functions.
    
    Returns:
        str: Comprehensive status information including:
             - Connector name and ARN
             - Current operational state (RUNNING/CREATING/FAILED/etc.)
             - Creation timestamp and current version
             - Kafka Connect version in use
             - Human-readable status interpretation
    
    States Explained:
        - RUNNING: Connector is operational and processing data
        - CREATING: Connector is being set up (normal during creation)
        - FAILED: Connector has failed (check logs and configuration)
        - DELETING: Connector is being removed
        - UPDATING: Connector configuration is being modified
    
    Example:
        check_connector_status("arn:aws:kafkaconnect:region:account:connector/name/uuid")
    """
    try:
        validate_required_params({"connector_arn": connector_arn}, ["connector_arn"])
        
        client = get_kafka_connect_client()
        response = client.describe_connector(connectorArn=connector_arn)
        
        connector = response['connectorDescription']
        connector_state = connector['connectorState']
        connector_name = connector['connectorName']
        
        status_info = f"Connector: {connector_name}\n"
        status_info += f"ARN: {connector_arn}\n"
        status_info += f"State: {connector_state}\n"
        status_info += f"Creation Time: {connector.get('creationTime', 'N/A')}\n"
        status_info += f"Current Version: {connector.get('currentVersion', 'N/A')}\n"
        status_info += f"Kafka Connect Version: {connector.get('kafkaConnectVersion', 'N/A')}\n"
        
        if connector_state == 'RUNNING':
            status_info += "✅ Connector is running normally and processing data"
        elif connector_state == 'CREATING':
            status_info += "⏳ Connector is being created, please check again in a few minutes"
        elif connector_state == 'FAILED':
            status_info += "❌ Connector has failed - check configuration and CloudWatch logs"
        elif connector_state == 'DELETING':
            status_info += "🗑️ Connector is being deleted"
        elif connector_state == 'UPDATING':
            status_info += "🔄 Connector is being updated"
        
        return status_info
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to check connector status ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to check connector status: {str(e)}"

@mcp.tool()
def wait_for_connector_ready(connector_arn: str, max_wait_minutes: int = 15) -> str:
    """
    Tool: Wait for connector to reach RUNNING state with automatic polling.
    
    This tool continuously monitors a connector's status until it reaches the
    RUNNING state or fails. Essential for automated workflows where you need
    to ensure connector readiness before proceeding with dependent operations.
    
    Use Case:
    - Automated MirrorMaker2 setup workflows
    - Ensuring connector readiness before creating dependent connectors
    - Avoiding manual status checking in orchestration scripts
    - Error detection during connector startup
    
    Args:
        connector_arn (str): AWS ARN of the connector to monitor.
                            Obtained from connector creation functions.
        max_wait_minutes (int): Maximum time to wait in minutes before timing out.
                               Defaults to 15 minutes (typical connector startup time).
    
    Returns:
        str: Final status message indicating success, failure, or timeout.
             Success: "✅ Connector successfully reached target state: RUNNING"
             Failure: "❌ Connector failed with state: [state]" or timeout message
    
    Polling Behavior:
        - Checks connector status every 30 seconds
        - Returns immediately on RUNNING state (success)
        - Returns immediately on FAILED or DELETING (failure)
        - Times out after max_wait_minutes with informative message
    
    Example:
        wait_for_connector_ready("arn:aws:kafkaconnect:region:account:connector/name/uuid", 20)
    """
    try:
        validate_required_params({"connector_arn": connector_arn}, ["connector_arn"])
        
        client = get_kafka_connect_client()
        
        def check_connector_state():
            response = client.describe_connector(connectorArn=connector_arn)
            return response['connectorDescription']['connectorState']
        
        result = wait_for_resource_state(
            check_function=check_connector_state,
            target_states=['RUNNING'],
            failure_states=['FAILED', 'DELETING'],
            max_wait_seconds=max_wait_minutes * 60,
            check_interval=30,
            resource_name="Connector"
        )
        
        if result['success']:
            return f"✅ {result['message']}"
        else:
            return f"❌ {result['message']}"
    
    except Exception as e:
        return f"Failed to wait for connector status: {str(e)}"

@mcp.tool()
def list_connectors() -> str:
    """
    List all MSK Connect connectors
    
    Returns:
        List of all connectors with basic information
    """
    try:
        client = get_kafka_connect_client()
        response = client.list_connectors()
        
        if not response['connectors']:
            return "No connectors found"
        
        result = "MSK Connect Connectors:\n"
        for connector in response['connectors']:
            result += f"- Name: {connector['connectorName']}\n"
            result += f"  ARN: {connector['connectorArn']}\n"
            result += f"  State: {connector['connectorState']}\n"
            result += f"  Description: {connector.get('connectorDescription', 'N/A')}\n"
            result += f"  Creation Time: {connector['creationTime']}\n"
            result += f"  Current Version: {connector['currentVersion']}\n\n"
        
        return result
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to list connectors ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to list connectors: {str(e)}"

@mcp.tool()
def describe_connector(connector_arn: str) -> str:
    """
    Describe a specific MSK Connect connector
    
    Args:
        connector_arn: Connector ARN
    
    Returns:
        Connector details
    """
    try:
        validate_required_params({"connector_arn": connector_arn}, ["connector_arn"])
        
        client = get_kafka_connect_client()
        response = client.describe_connector(connectorArn=connector_arn)
        
        connector = response['connectorDescription']
        
        result = f"Connector Details:\n"
        result += f"Name: {connector['connectorName']}\n"
        result += f"ARN: {connector['connectorArn']}\n"
        result += f"State: {connector['connectorState']}\n"
        result += f"Description: {connector.get('connectorDescription', 'N/A')}\n"
        result += f"Kafka Connect版本: {connector['kafkaConnectVersion']}\n"
        result += f"Creation Time: {connector['creationTime']}\n"
        result += f"Current Version: {connector['currentVersion']}\n"
        result += f"Service Execution Role: {connector['serviceExecutionRoleArn']}\n"
        
        if 'connectorConfiguration' in connector:
            result += f"\nConfiguration:\n"
            for key, value in connector['connectorConfiguration'].items():
                # Mask sensitive information
                if any(sensitive in key.lower() for sensitive in ['password', 'secret', 'key']):
                    value = "***MASKED***"
                result += f"  {key}: {value}\n"
        
        return result
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to describe connector ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to describe connector: {str(e)}"

@mcp.tool()
def list_custom_plugins() -> str:
    """
    List all MSK Connect custom plugins
    
    Returns:
        Plugins list
    """
    try:
        client = get_kafka_connect_client()
        response = client.list_custom_plugins()

        if not response['customPlugins']:
            return "No custom plugins found"
        
        result = "MSK Connect Custom Plugins:\n"
        for plugin in response['customPlugins']:
            result += f"- Name: {plugin['name']}\n"
            result += f"  ARN: {plugin['customPluginArn']}\n"
            result += f"  State: {plugin['customPluginState']}\n"
            result += f"  Description: {plugin.get('description', 'N/A')}\n"
            result += f"  Creation Time: {plugin['creationTime']}\n"
            result += f"  Latest Revision: {plugin['latestRevision']['revision']}\n\n"
        
        return result
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to list plugins ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to list plugins: {str(e)}"

@mcp.tool()
def describe_custom_plugin(plugin_arn: str) -> str:
    """
    Describe a specific MSK Connect custom plugin
    
    Args:
        plugin_arn: Plugin的ARN
    
    Returns:
        Plugin详细信息
    """
    try:
        validate_required_params({"plugin_arn": plugin_arn}, ["plugin_arn"])
        
        client = get_kafka_connect_client()
        response = client.describe_custom_plugin(customPluginArn=plugin_arn)
        
        plugin = response
        result = f"Plugin Details:\n"
        result += f"Name: {plugin['name']}\n"
        result += f"ARN: {plugin['customPluginArn']}\n"
        result += f"State: {plugin['customPluginState']}\n"
        result += f"Description: {plugin.get('description', 'N/A')}\n"
        result += f"Creation Time: {plugin['creationTime']}\n"
        result += f"Latest Revision: {plugin['latestRevision']['revision']}\n"
        
        if 'latestRevision' in plugin:
            revision = plugin['latestRevision']
            if 'location' in revision and 's3Location' in revision['location']:
                s3_location = revision['location']['s3Location']
                result += f"S3 Location: {s3_location['bucketArn']}/{s3_location['fileKey']}\n"
        
        return result
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to describe plugin ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to describe plugin: {str(e)}"

@mcp.tool()
def list_worker_configurations() -> str:
    """
    List all MSK Connect worker configurations
    
    Returns:
        Worker configurations list
    """
    try:
        client = get_kafka_connect_client()
        response = client.list_worker_configurations()
        
        if not response['workerConfigurations']:
            return "No worker configurations found"
        
        result = "MSK Connect WorkerConfiguration:\n"
        for config in response['workerConfigurations']:
            result += f"- Name: {config['name']}\n"
            result += f"  ARN: {config['workerConfigurationArn']}\n"
            result += f"  State: {config['workerConfigurationState']}\n"
            result += f"  Description: {config.get('description', 'N/A')}\n"
            result += f"  Creation Time: {config['creationTime']}\n"
            result += f"  Latest Revision: {config['latestRevision']['revision']}\n\n"
        
        return result
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to list worker configurations ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to list worker configurations: {str(e)}"

@mcp.tool()
def describe_worker_configuration(worker_config_arn: str) -> str:
    """
    Describe a specific MSK Connect worker configuration
    
    Args:
        worker_config_arn: Worker ARN
    
    Returns:
        Worker configuration details
    """
    try:
        validate_required_params({"worker_config_arn": worker_config_arn}, ["worker_config_arn"])
        
        client = get_kafka_connect_client()
        response = client.describe_worker_configuration(workerConfigurationArn=worker_config_arn)
        
        config = response['workerConfigurationDescription']
        
        result = f"Worker Configuration Details:\n"
        result += f"Name: {config['name']}\n"
        result += f"ARN: {config['workerConfigurationArn']}\n"
        result += f"State: {config['workerConfigurationState']}\n"
        result += f"Description: {config.get('description', 'N/A')}\n"
        result += f"Creation Time: {config['creationTime']}\n"
        result += f"Latest Revision: {config['latestRevision']['revision']}\n"
        
        if 'latestRevision' in config and 'propertiesFileContent' in config['latestRevision']:
            result += f"\nConfiguration Content:\n{config['latestRevision']['propertiesFileContent']}\n"
        
        return result
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to describe worker configuration ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to describe worker configuration: {str(e)}"

@mcp.tool()
def update_connector(
    connector_arn: str,
    connector_configuration: Dict[str, str],
    current_version: str,
    capacity: Optional[Dict[str, Any]] = None
) -> str:
    """
    Update MSK Connect connector
    
    Args:
        connector_arn: Connector ARN
        connector_configuration: New connector configuration
        current_version: Current version
        capacity: New capacity configuration (optional)
    
    Returns:
        Update result information
    """
    try:
        validate_required_params(
            {"connector_arn": connector_arn, "connector_configuration": connector_configuration, "current_version": current_version},
            ["connector_arn", "connector_configuration", "current_version"]
        )
        
        if not isinstance(connector_configuration, dict):
            return "Error: connector_configuration must be in dictionary format"
        
        client = get_kafka_connect_client()
        
        request_params = {
            "connectorArn": connector_arn,
            "currentVersion": current_version,
            "capacity": capacity or {
                "provisionedCapacity": {
                    "mcuCount": 1,
                    "workerCount": 1
                }
            },
            "connectorConfiguration": connector_configuration
        }
        
        response = client.update_connector(**request_params)
        
        return f"Successfully updated connector: {response['connectorArn']}, State: {response['connectorState']}"
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to update connector ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to update connector: {str(e)}"

@mcp.tool()
def delete_connector(connector_arn: str, current_version: str) -> str:
    """
    Delete MSK Connect connector
    
    Args:
        connector_arn: Connector ARN
        current_version: Current version
    
    Returns:
        Delete result information
    """
    try:
        validate_required_params(
            {"connector_arn": connector_arn, "current_version": current_version},
            ["connector_arn", "current_version"]
        )
        
        client = get_kafka_connect_client()
        
        response = client.delete_connector(
            connectorArn=connector_arn,
            currentVersion=current_version
        )
        
        return f"Successfully deleted connector: {response['connectorArn']}, State: {response['connectorState']}"
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to delete connector ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to delete connector: {str(e)}"

@mcp.tool()
def delete_custom_plugin(plugin_arn: str) -> str:
    """
    Delete MSK Connect custom plugin
    
    Args:
        plugin_arn: Plugin ARN
    
    Returns:
        Delete result information
    """
    try:
        validate_required_params({"plugin_arn": plugin_arn}, ["plugin_arn"])
        
        client = get_kafka_connect_client()
        response = client.delete_custom_plugin(customPluginArn=plugin_arn)
        
        return f"Successfully deleted plugin: {response['customPluginArn']}, State: {response['customPluginState']}"
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to delete plugin ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to delete plugin: {str(e)}"

@mcp.tool()
def delete_worker_configuration(worker_config_arn: str) -> str:
    """
    Delete MSK Connect worker configuration
    
    Args:
        worker_config_arn: Worker configuration ARN
    
    Returns:
        Delete result information
    """
    try:
        validate_required_params({"worker_config_arn": worker_config_arn}, ["worker_config_arn"])
        
        client = get_kafka_connect_client()
        response = client.delete_worker_configuration(workerConfigurationArn=worker_config_arn)
        
        return f"Successfully deleted workerConfiguration: {response['workerConfigurationArn']}, State: {response['workerConfigurationState']}"
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        return f"Failed to delete worker configuration ({error_code}): {str(e)}"
    except Exception as e:
        return f"Failed to delete worker configuration: {str(e)}"

# High-level orchestration function
@mcp.tool()
def create_complete_mm2_setup(
    plugin_name: str,
    s3_bucket: str,
    s3_key: str,
    source_bootstrap_servers: str,
    target_bootstrap_servers: str,
    service_execution_role_arn: str,
    vpc_subnets: List[str],
    vpc_security_groups: List[str],
    source_auth_type: str = "iam",
    target_auth_type: str = "iam",
    source_role_arn: Optional[str] = None,
    target_role_arn: Optional[str] = None
) -> str:
    """
    Create complete MirrorMaker2 setup (Plugin + 3 Connectors)
    High-level orchestration function that creates all necessary components in sequence
    """
    try:
        # Validate all required parameters
        required_params = {
            "plugin_name": plugin_name,
            "s3_bucket": s3_bucket,
            "s3_key": s3_key,
            "source_bootstrap_servers": source_bootstrap_servers,
            "target_bootstrap_servers": target_bootstrap_servers,
            "service_execution_role_arn": service_execution_role_arn,
            "vpc_subnets": vpc_subnets,
            "vpc_security_groups": vpc_security_groups
        }
        validate_required_params(required_params, list(required_params.keys()))
        
        results = []
        
        # Step 1: Create Plugin
        results.append("Step 1: Creating custom plugin...")
        plugin_result = create_custom_plugin(plugin_name, s3_bucket, s3_key)
        results.append(plugin_result)
        
        if "Failed" in plugin_result:
            return "\n".join(results)
        
        # Extract plugin ARN from result
        try:
            plugin_arn = plugin_result.split("ARN: ")[1].split(",")[0]
        except IndexError:
            return "\n".join(results + ["Error: Unable to extract ARN from plugin creation result"])
        
        # Step 2: Wait for plugin to be ready
        results.append("\nStep 2: Waiting for plugin to be ready...")
        wait_result = wait_for_plugin_ready(plugin_arn, max_wait_minutes=10)
        results.append(wait_result)
        
        if "❌" in wait_result:
            return "\n".join(results)
        
        # Step 3: Create Heartbeat Connector
        results.append("\nStep 3: Creating MirrorHeartbeatConnector...")
        heartbeat_result = create_mirror_heartbeat_connector(
            f"{plugin_name}-heartbeat",
            source_bootstrap_servers,
            target_bootstrap_servers,
            plugin_arn,
            service_execution_role_arn,
            vpc_subnets,
            vpc_security_groups,
            source_auth_type,
            target_auth_type,
            source_role_arn,
            target_role_arn
        )
        results.append(heartbeat_result)
        
        # Step 4: Create Checkpoint Connector
        results.append("\nStep 4: Creating MirrorCheckpointConnector...")
        checkpoint_result = create_mirror_checkpoint_connector(
            f"{plugin_name}-checkpoint",
            source_bootstrap_servers,
            target_bootstrap_servers,
            plugin_arn,
            service_execution_role_arn,
            vpc_subnets,
            vpc_security_groups,
            source_auth_type,
            target_auth_type,
            source_role_arn,
            target_role_arn
        )
        results.append(checkpoint_result)
        
        # Step 5: Create Source Connector
        results.append("\nStep 5: Creating MirrorSourceConnector...")
        source_result = create_mirror_source_connector(
            f"{plugin_name}-source",
            source_bootstrap_servers,
            target_bootstrap_servers,
            plugin_arn,
            service_execution_role_arn,
            vpc_subnets,
            vpc_security_groups,
            source_auth_type,
            target_auth_type,
            source_role_arn,
            target_role_arn
        )
        results.append(source_result)
        
        results.append("\n✅ MirrorMaker2 complete setup creation finished!")
        results.append("\nRecommended verification steps:")
        results.append("1. Use check_connector_status to verify all component states")
        results.append("2. Verify heartbeat topic creation")
        results.append("3. Check if data replication is working properly")
        
        return "\n".join(results)
        
    except Exception as e:
        return f"Error occurred while creating complete setup: {str(e)}"

# Entry point will be handled by __init__.py
