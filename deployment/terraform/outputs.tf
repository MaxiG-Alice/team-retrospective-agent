output "cloud_run_url" {
  description = "Deployed Cloud Run service URL"
  value       = google_cloud_run_v2_service.retro_agent.uri
}

output "artifacts_bucket" {
  description = "GCS bucket for uploaded photos and generated collages"
  value       = google_storage_bucket.retro_artifacts.name
}

output "service_account_email" {
  description = "Service account running the retrospective agent"
  value       = google_service_account.retro_agent_sa.email
}
