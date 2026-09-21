terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    bucket = "ats-terraform-state"
    key    = "ats/terraform.tfstate"
    region = "ap-southeast-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# --- VPC ---
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  tags = { Name = "ats-vpc", Project = "ats" }
}

resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "${var.aws_region}a"
  tags = { Name = "ats-private-a" }
}

resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "${var.aws_region}b"
  tags = { Name = "ats-private-b" }
}

# --- RDS PostgreSQL ---
resource "aws_db_subnet_group" "main" {
  name       = "ats-db-subnet"
  subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_b.id]
}

resource "aws_db_instance" "postgres" {
  identifier             = "ats-postgres"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = var.db_instance_class
  allocated_storage      = 20
  storage_type           = "gp3"
  db_name                = "ats"
  username               = "ats"
  password               = random_password.db_password.result
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  skip_final_snapshot    = var.environment == "dev"
  deletion_protection    = var.environment == "prod"
  backup_retention_period = 7
  tags                   = { Project = "ats", Environment = var.environment }
}

resource "random_password" "db_password" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "db_password" {
  name = "ats/db-password"
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = random_password.db_password.result
}

# --- Security Groups ---
resource "aws_security_group" "db" {
  name   = "ats-db-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
}

resource "aws_security_group" "app" {
  name   = "ats-app-sg"
  vpc_id = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- SNS Topic for Alerts ---
resource "aws_sns_topic" "alerts" {
  name = "ats-alerts"
  tags = { Project = "ats" }
}

# --- ECS Cluster ---
resource "aws_ecs_cluster" "main" {
  name = "ats-cluster"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  tags = { Project = "ats" }
}

# --- CloudWatch Log Group ---
resource "aws_cloudwatch_log_group" "ats" {
  name              = "/ats/application"
  retention_in_days = 30
  tags              = { Project = "ats" }
}

resource "aws_cloudwatch_metric_alarm" "application_errors" {
  alarm_name          = "ats-application-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ErrorCount"
  namespace           = "ATS"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "ATS application error rate exceeded threshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# --- S3 Bucket for backtest data and logs ---
resource "aws_s3_bucket" "data" {
  bucket = "ats-data-${var.environment}"
  tags   = { Project = "ats", Environment = var.environment }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
