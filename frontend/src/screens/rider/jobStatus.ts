import type { JobView } from "../../lib/types";

export function jobHeadline(job: Pick<JobView, "status" | "offering" | "weight_g">): string {
  switch (job.status) {
    case "assigned":
      return "Go to the pickup";
    case "arrived":
      if (job.offering === "refuse") return "Collect the waste";
      return job.weight_g == null ? "Weigh and photograph the plastic" : "Retake the photo";
    case "location_issue":
      return "Call the household for directions";
    case "verifying":
      return "Checking the photo";
    case "rider_review":
      return "Check the plastic yourself";
    case "awaiting_pin":
      return "Ask for the household's PIN";
    default:
      return "Job";
  }
}
