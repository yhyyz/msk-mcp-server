# MSK MirrorMaker2 MCP Server

A Model Context Protocol server that provides comprehensive management capabilities for Amazon MSK (Managed Streaming for Apache Kafka) MirrorMaker2 connectors. This server enables LLMs to create, configure, and manage complete MirrorMaker2 replication pipelines across MSK clusters.

> [!CAUTION]
> This server requires AWS credentials with permissions to access MSK Connect, S3, and related AWS services. Exercise caution when using this MCP server to ensure proper AWS security practices.

The server provides tools for orchestrating complete MirrorMaker2 setups, including custom plugin creation, connector configuration, and status monitoring. It supports multiple authentication methods (IAM, PLAINTEXT, SCRAM-SHA-512) and handles long-running operations with proper timeouts and retries.

## Features

### Core MirrorMaker2 Management
- **Complete MM2 Pipeline Setup** - Orchestrates full MirrorMaker2 replication setup with three connector types (Heartbeat, Checkpoint, Source)
- **Custom Plugin Management** - Creates and manages MSK Connect custom plugins with automatic S3 integration
- **Authentication Support** - Configures IAM, PLAINTEXT, and SCRAM-SHA-512 authentication for source and target clusters
- **Status Monitoring** - Real-time monitoring of connector and plugin states with automated waiting functions

### Available Tools

#### Plugin Management
- `create_empty_plugin_zip` - Creates an empty MirrorMaker2 plugin ZIP file and uploads to S3
- `create_custom_plugin` - Creates MSK Connect custom plugin from S3-stored ZIP file
- `check_plugin_status` - Checks the current status of a custom plugin
- `wait_for_plugin_ready` - Waits for plugin to reach ACTIVE state with timeout handling

#### Connector Management
- `create_mirror_heartbeat_connector` - Creates MirrorHeartbeatConnector for cluster monitoring
- `create_mirror_checkpoint_connector` - Creates MirrorCheckpointConnector for offset tracking
- `create_mirror_source_connector` - Creates MirrorSourceConnector for data replication
- `check_connector_status` - Monitors connector operational status
- `wait_for_connector_ready` - Waits for connector to reach RUNNING state

#### Worker Configuration
- `create_worker_configuration` - Creates MSK Connect worker configurations
- `describe_worker_configuration` - Retrieves worker configuration details
- `delete_worker_configuration` - Removes worker configurations

#### High-Level Operations
- `create_complete_mm2_setup` - End-to-end MirrorMaker2 setup orchestration
- `update_connector` - Updates existing connector configurations
- `delete_connector` - Safely removes connectors
- `list_connectors` - Lists all MSK Connect connectors
- `describe_connector` - Detailed connector information

### Available Resources
- `msk://connectors` - Lists all MSK Connect connectors
- `msk://connectors/{connector_name}` - Detailed information about specific connectors
- `msk://plugins` - Lists all custom plugins
- `msk://worker-configs` - Lists all worker configurations
- `msk://connector-status/{connector_name}` - Real-time connector status information

### Available Prompts
- **setup_complete_mm2_replication** - Guidance for setting up complete MirrorMaker2 replication between clusters
- **troubleshoot_connector** - Troubleshooting guidance for connector issues
- **optimize_mm2_performance** - Performance optimization recommendations for different scales

## Prerequisites

1. **AWS Account Setup**
   - AWS account with appropriate permissions
   - MSK clusters (source and target) properly configured
   - VPC subnets and security groups configured for MSK Connect
   - IAM service execution role for MSK Connect

2. **Required AWS Permissions**
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "kafkaconnect:*",
           "s3:GetObject",
           "s3:PutObject",
           "s3:ListBucket"
         ],
         "Resource": "*"
       }
     ]
   }
   ```

3. **S3 Bucket**
   - S3 bucket for storing MirrorMaker2 plugin files
   - Proper IAM permissions for MSK Connect to access the bucket

4. **Network Configuration**
   - VPC subnets accessible to both source and target MSK clusters
   - Security groups allowing MSK Connect to communicate with clusters

## Installation

### Using uv (recommended)

When using [`uv`](https://docs.astral.sh/uv/) no specific installation is needed. We will use [`uvx`](https://docs.astral.sh/uv/guides/tools/) to directly run *msk-mcp-server*.

### Using PIP

Alternatively you can install `msk-mcp-server` via pip:

```bash
pip install msk-mcp-server
```

After installation, you can run it as a script using:

```bash
python -m mcp_server_msk_mm2
```

## Configuration

### Configure for Claude.app

Add to your Claude settings:

**Using uvx with latest version**
```json
{
  "mcpServers": {
    "msk-mcp-server": {
      "command": "uvx",
      "args": ["msk-mcp-server@latest"],
      "env": {
        "AWS_REGION": "us-east-1",
        "AWS_PROFILE": "your-aws-profile",
        "FASTMCP_LOG_LEVEL": "ERROR"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

**Using uvx (simple)**
```json
{
  "mcpServers": {
    "msk-mm2": {
      "command": "uvx",
      "args": ["msk-mcp-server"],
      "env": {
        "AWS_REGION": "us-east-1",
        "AWS_PROFILE": "your-aws-profile"
      }
    }
  }
}
```

**Using pip installation**
```json
{
  "mcpServers": {
    "msk-mm2": {
      "command": "python",
      "args": ["-m", "mcp_server_msk_mm2"],
      "env": {
        "AWS_REGION": "us-east-1",
        "AWS_PROFILE": "your-aws-profile"
      }
    }
  }
}
```

### Configure for VS Code

For manual installation, add the following JSON block to your User Settings (JSON) file in VS Code. You can do this by pressing `Ctrl + Shift + P` and typing `Preferences: Open User Settings (JSON)`.

Optionally, you can add it to a file called `.vscode/mcp.json` in your workspace. This will allow you to share the configuration with others.

> Note that the `mcp` key is needed when using the `mcp.json` file.

**Using uvx with latest version**
```json
{
  "mcp": {
    "servers": {
      "msk-mcp-server": {
        "command": "uvx",
        "args": ["msk-mcp-server@latest"],
        "env": {
          "AWS_REGION": "us-east-1",
          "AWS_PROFILE": "your-aws-profile",
          "FASTMCP_LOG_LEVEL": "ERROR"
        },
        "disabled": false,
        "autoApprove": []
      }
    }
  }
}
```

**Using uvx (simple)**
```json
{
  "mcp": {
    "servers": {
      "msk-mm2": {
        "command": "uvx",
        "args": ["msk-mcp-server"],
        "env": {
          "AWS_REGION": "us-east-1",
          "AWS_PROFILE": "your-aws-profile"
        }
      }
    }
  }
}
```

## Environment Variables

### AWS Configuration
- `AWS_REGION` - AWS region for MSK Connect operations (default: uses AWS SDK default)
- `AWS_PROFILE` - AWS profile to use for authentication
- `AWS_ACCESS_KEY_ID` - AWS access key (if not using profiles)
- `AWS_SECRET_ACCESS_KEY` - AWS secret key (if not using profiles)

### Timeout Configuration
- `AWS_READ_TIMEOUT` - Read timeout for AWS operations in seconds (default: 300)
- `AWS_CONNECT_TIMEOUT` - Connection timeout for AWS operations in seconds (default: 60)

## Usage Examples

### Complete MirrorMaker2 Setup
The server provides a high-level orchestration tool for complete setup:

```json
{
  "tool": "create_complete_mm2_setup",
  "arguments": {
    "plugin_name": "my-mm2-plugin",
    "s3_bucket": "my-kafka-plugins",
    "s3_key": "mm2/mirrormaker2.zip",
    "source_bootstrap_servers": "source-cluster.kafka.us-east-1.amazonaws.com:9092",
    "target_bootstrap_servers": "target-cluster.kafka.us-east-1.amazonaws.com:9092",
    "service_execution_role_arn": "arn:aws:iam::123456789012:role/MSKConnectRole",
    "vpc_subnets": ["subnet-12345", "subnet-67890"],
    "vpc_security_groups": ["sg-abcdef"],
    "source_auth_type": "iam",
    "target_auth_type": "iam"
  }
}
```

### Monitor Connector Status
```json
{
  "tool": "check_connector_status",
  "arguments": {
    "connector_arn": "arn:aws:kafkaconnect:us-east-1:123456789012:connector/my-connector/abc-123"
  }
}
```

## Authentication Methods

### IAM Authentication
```json
{
  "source_auth_type": "iam",
  "source_role_arn": "arn:aws:iam::123456789012:role/MSKSourceRole"
}
```

### SCRAM-SHA-512 Authentication
```json
{
  "source_auth_type": "scram-sha-512",
  "source_username": "kafka-user",
  "source_password": "kafka-password"
}
```

### PLAINTEXT Authentication
```json
{
  "source_auth_type": "plaintext",
  "source_username": "kafka-user",
  "source_password": "kafka-password"
}
```

## Debugging

You can use the MCP inspector to debug the server:

For uvx installations:
```bash
npx @modelcontextprotocol/inspector uvx msk-mcp-server
```

Or if you've installed the package in a specific directory or are developing on it:
```bash
cd path/to/msk-mcp-server
npx @modelcontextprotocol/inspector python -m mcp_server_msk_mm2
```

## Architecture

The server is built with:
- **Sync Operations** - All operations are synchronous for better reliability
- **Comprehensive Error Handling** - Detailed error messages with AWS error codes
- **Status Monitoring** - Built-in polling for long-running operations
- **Retry Logic** - Exponential backoff for transient failures
- **Resource Validation** - Parameter validation and state checking

## Contributing

We encourage contributions to help expand and improve msk-mcp-server. Whether you want to add new tools, enhance existing functionality, or improve documentation, your input is valuable.

For examples of other MCP servers and implementation patterns, see:
https://github.com/modelcontextprotocol/servers

Pull requests are welcome! Feel free to contribute new ideas, bug fixes, or enhancements to make msk-mcp-server even more powerful and useful.

## License

msk-mcp-server is licensed under the MIT License. This means you are free to use, modify, and distribute the software, subject to the terms and conditions of the MIT License. For more details, please see the LICENSE file in the project repository. 