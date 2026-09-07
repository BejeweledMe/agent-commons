import assert from "node:assert/strict";
import test from "node:test";
import { MAX_IMAGE_BYTES, importMayHaveWritten, parseScreenImport, validScreenFile } from "../src/screenImportState.ts";

test("partial conflicts and unknown failures retain the original import identity", () => {
  for (const status of [401, 403, 404, 408, 409, 429, 500, 503]) {
    assert.equal(importMayHaveWritten(status), true);
  }
  for (const status of [400, 413, 415, 422]) {
    assert.equal(importMayHaveWritten(status), false);
  }
});

test("screen upload bounds actual file size and refuses active image formats", () => {
  assert.equal(validScreenFile({size: MAX_IMAGE_BYTES, type: "image/png"}), true);
  for (const file of [null, {size: 0, type: "image/png"}, {size: MAX_IMAGE_BYTES + 1, type: "image/jpeg"},
    {size: 10, type: "image/svg+xml"}, {size: 10, type: "text/html"}]) assert.equal(validScreenFile(file), false);
});

test("import success is a bounded exact candidate, not a path or guessed acceptance", () => {
  const ulid = "01ARZ3NDEKTSV4RRFFQ69G5FAV";
  const result = {schema: "agent_commons.gallery-import.v1", state: "imported",
    artifact_id: `artifact.${ulid}`, task_id: `task.${ulid}`, candidate_id: `candidate.${ulid}`};
  assert.deepEqual(parseScreenImport(result), {candidateId: result.candidate_id});
  for (const altered of [null, [], {...result, state: "accepted"}, {...result, candidate_id: "../image.png"},
    {...result, task_id: "task.missing"}, {...result, content_base64: "hidden"}]) {
    assert.throws(() => parseScreenImport(altered), /invalid_import_response/);
  }
});
