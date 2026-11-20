variable "project_id" {
  description = "Google Cloud Project ID"
  type        = string
  default     = "mglaesle-sandbox"
}

variable "region" {
  description = "Google Cloud region for Cloud Run and Vertex AI"
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "team-retrospective-agent"
}

variable "container_image" {
  description = "Container image URI for the ADK agent service"
  type        = string
  default     = "us-central1-docker.pkg.dev/mglaesle-sandbox/agents/team-retrospective-agent:latest"
}
