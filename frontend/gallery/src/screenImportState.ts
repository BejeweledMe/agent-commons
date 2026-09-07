export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

// Only these input refusals guarantee that the server did not start an import.
// A conflict may follow a successful task creation or artifact registration.
export function importMayHaveWritten(status: number): boolean {
  return ![400, 413, 415, 422].includes(status);
}

export function validScreenFile(file: {size: number; type: string} | null): boolean {
  return file !== null && Number.isSafeInteger(file.size) && file.size > 0
    && file.size <= MAX_IMAGE_BYTES && ["image/png", "image/jpeg"].includes(file.type);
}

export function parseScreenImport(value: unknown): {candidateId: string} {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid_import_response");
  const item = value as Record<string, unknown>;
  const id = (kind: string, value: unknown): boolean => typeof value === "string"
    && new RegExp(`^${kind}\\.[0-9A-HJKMNP-TV-Z]{26}$`).test(value);
  if (Object.keys(item).sort().join(",") !== "artifact_id,candidate_id,schema,state,task_id"
    || item.schema !== "agent_commons.gallery-import.v1" || item.state !== "imported"
    || !id("candidate", item.candidate_id) || !id("artifact", item.artifact_id) || !id("task", item.task_id)) {
    throw new Error("invalid_import_response");
  }
  return {candidateId: item.candidate_id as string};
}
