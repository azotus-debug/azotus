import { useEffect } from "react";
import type { ReviewData } from "../types";
import { useReviewStore } from "../store/reviewStore";

interface ReviewDataOptions {
  mockData?: ReviewData;
}

function getJobFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const pathParts = window.location.pathname.split("/").filter(Boolean);
  const jobId = pathParts[pathParts.length - 1] || "";
  return {
    jobId,
    token: params.get("token"),
    exp: params.get("exp"),
  };
}

export function useReviewData({ mockData }: ReviewDataOptions) {
  const { setData, setLoading, setError } = useReviewStore();

  useEffect(() => {
    if (mockData) {
      setData(mockData);
      return;
    }

    const { jobId, token, exp } = getJobFromUrl();
    if (!jobId) {
      setError("Missing job id");
      return;
    }

    setLoading(true);

    fetch(`/api/job/${jobId}/review-summary?token=${token ?? ""}&exp=${exp ?? ""}`)
      .then((res) => {
        if (!res.ok) {
          throw new Error(`Request failed: ${res.status}`);
        }
        return res.json();
      })
      .then((data: ReviewData) => setData(data))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load review"))
      .finally(() => setLoading(false));
  }, [mockData, setData, setLoading, setError]);
}
