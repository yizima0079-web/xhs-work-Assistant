// datapp MongoDB Atlas schema and indexes.
// Run with: mongosh "$DATAPP_MONGODB_URI" --file server/mongodb_schema.js
// The script is idempotent: it creates collections or updates validators/indexes.

const databaseName = "datapp";
const target = db.getSiblingDB(databaseName);

function nullableString() { return { bsonType: ["string", "null"] }; }
function nullableNumber() { return { bsonType: ["int", "long", "double", "null"] }; }
function stringArray() { return { bsonType: "array", items: { bsonType: "string" } }; }
function jsonObject() { return { bsonType: "object" }; }

const schemas = {
  collection_runs: {
    required: ["_id", "platform", "target_type", "target", "max_items", "status", "items_found", "items_saved"],
    properties: {
      _id: { bsonType: "string" }, platform: { bsonType: "string" }, target_type: { bsonType: "string" },
      target: { bsonType: "string" }, max_items: { bsonType: ["int", "long"] }, status: { bsonType: "string" },
      request_id: nullableString(), started_at: nullableString(), finished_at: nullableString(),
      error_code: nullableString(), error_category: nullableString(), items_found: { bsonType: ["int", "long"] },
      items_saved: { bsonType: ["int", "long"] }, retry_count: { bsonType: ["int", "long"] },
      rate_limit_signal: { bsonType: ["int", "long"] }, tool_version: nullableString()
    }
  },
  collection_events: {
    required: ["_id", "run_id", "seq", "ts", "level"],
    properties: {
      _id: { bsonType: "string" }, run_id: { bsonType: "string" }, seq: { bsonType: ["int", "long"] },
      ts: { bsonType: "string" }, level: { bsonType: "string" }, category: nullableString(),
      code: nullableString(), message: nullableString()
    }
  },
  contents: {
    required: ["_id", "platform", "platform_item_id", "collected_at", "review_status"],
    properties: {
      _id: { bsonType: "string" }, platform: { bsonType: "string" }, platform_item_id: { bsonType: "string" },
      content_type: nullableString(), canonical_url: nullableString(), author_id: nullableString(), author_name: nullableString(),
      published_at: nullableString(), collected_at: { bsonType: "string" }, title: nullableString(), text: nullableString(),
      cover_url: nullableString(), cover_local: nullableString(), tags: stringArray(), media: { bsonType: "array" },
      engagement: jsonObject(), raw_refs: stringArray(), review_status: { bsonType: "string" }, active_batch_id: nullableString()
    }
  },
  raw_assets: {
    required: ["_id", "bytes", "collected_at"],
    properties: {
      _id: { bsonType: "string" }, run_id: nullableString(), content_id: nullableString(), kind: nullableString(),
      mime: nullableString(), sha256: nullableString(), bytes: { bsonType: ["int", "long"] }, storage_path: nullableString(),
      collected_at: { bsonType: "string" }
    }
  },
  claims: {
    required: ["_id", "content_id", "text", "status", "batch_id", "batch_seq", "created_at", "updated_at"],
    properties: {
      _id: { bsonType: "string" }, content_id: { bsonType: "string" }, text: { bsonType: "string" }, status: { bsonType: "string" },
      confidence: nullableNumber(), claim_type: nullableString(), meta: jsonObject(), batch_id: { bsonType: "string" },
      batch_seq: { bsonType: ["int", "long"] }, created_at: { bsonType: "string" }, updated_at: { bsonType: "string" }
    }
  },
  evidence: {
    required: ["_id", "claim_id", "source_kind", "excerpt", "strength", "collected_at"],
    properties: {
      _id: { bsonType: "string" }, claim_id: { bsonType: "string" }, source_kind: { bsonType: "string" },
      source_ref: nullableString(), excerpt: { bsonType: "string" }, supports: { bsonType: ["bool", "null"] },
      strength: { bsonType: ["int", "long", "double"] }, collected_at: { bsonType: "string" }
    }
  },
  review_decisions: {
    required: ["_id", "claim_id", "status", "rationale", "reviewer", "evidence_ids", "created_at"],
    properties: {
      _id: { bsonType: "string" }, claim_id: { bsonType: "string" }, status: { bsonType: "string" },
      rationale: { bsonType: "string" }, reviewer: { bsonType: "string" }, evidence_ids: stringArray(), created_at: { bsonType: "string" }
    }
  },
  reports: {
    required: ["_id", "title", "content_ids", "payload", "markdown", "created_at", "schema_version"],
    properties: {
      _id: { bsonType: "string" }, title: { bsonType: "string" }, content_ids: stringArray(), payload: jsonObject(),
      markdown: { bsonType: "string" }, created_at: { bsonType: "string" }, schema_version: { bsonType: "string" }
    }
  },
  analyses: {
    required: ["_id", "content_id", "verdict", "payload", "markdown", "compared_with", "focus", "created_at", "schema_version"],
    properties: {
      _id: { bsonType: "string" }, content_id: { bsonType: "string" }, verdict: { bsonType: "string" }, payload: jsonObject(),
      markdown: { bsonType: "string" }, compared_with: stringArray(), focus: { bsonType: "string" }, created_at: { bsonType: "string" },
      schema_version: { bsonType: "string" }
    }
  },
  kb_documents: {
    required: ["_id", "source_type", "doc_type", "title", "tags", "content_hash", "raw_text", "markdown", "status", "chunk_count", "created_at"],
    properties: {
      _id: { bsonType: "string" }, source_type: { bsonType: "string" }, source_id: nullableString(), doc_type: { bsonType: "string" },
      title: { bsonType: "string" }, author: nullableString(), tags: stringArray(), url: nullableString(), content_hash: { bsonType: "string" },
      raw_text: { bsonType: "string" }, markdown: { bsonType: "string" }, status: { bsonType: "string" },
      chunk_count: { bsonType: ["int", "long"] }, created_at: { bsonType: "string" }
    }
  },
  kb_chunks: {
    required: ["_id", "doc_id", "chunk_index", "text", "embedding", "modality", "meta", "created_at"],
    properties: {
      _id: { bsonType: "string" }, doc_id: { bsonType: "string" }, chunk_index: { bsonType: ["int", "long"] },
      text: { bsonType: "string" }, embedding: { bsonType: "array", items: { bsonType: ["double", "int", "long"] } },
      modality: { bsonType: "string" }, image_url: nullableString(), meta: jsonObject(), created_at: { bsonType: "string" }
    }
  },
  kb_qa_pairs: {
    required: ["_id", "doc_id", "qa_index", "question", "answer", "dimensions", "evidence", "tags", "source_type", "status", "created_at", "updated_at"],
    properties: {
      _id: { bsonType: "string" }, doc_id: { bsonType: "string" }, qa_index: { bsonType: ["int", "long"] },
      question: { bsonType: "string" }, answer: { bsonType: "string" }, dimensions: jsonObject(), evidence: { bsonType: "array" },
      tags: stringArray(), source_type: { bsonType: "string" }, source_id: nullableString(), source_url: nullableString(),
      source_author: nullableString(), status: { bsonType: "string" }, created_at: { bsonType: "string" }, updated_at: { bsonType: "string" }
    }
  }
};

for (const [name, schema] of Object.entries(schemas)) {
  const validator = { $jsonSchema: { bsonType: "object", ...schema } };
  if (!target.getCollectionNames().includes(name)) {
    target.createCollection(name, { validator, validationLevel: "strict", validationAction: "error" });
  } else {
    target.runCommand({ collMod: name, validator, validationLevel: "moderate", validationAction: "error" });
  }
}

target.collection_runs.createIndex({ status: 1, finished_at: -1 });
target.collection_events.createIndex({ run_id: 1, seq: 1 }, { unique: true });
target.contents.createIndex({ platform: 1, platform_item_id: 1 }, { unique: true });
target.contents.createIndex({ review_status: 1, collected_at: -1 });
target.contents.createIndex({ author_id: 1, collected_at: -1 });
target.raw_assets.createIndex({ run_id: 1, collected_at: 1 });
target.raw_assets.createIndex({ sha256: 1 }, { sparse: true });
target.claims.createIndex({ content_id: 1, batch_seq: 1 });
target.claims.createIndex({ content_id: 1, batch_id: 1 });
target.evidence.createIndex({ claim_id: 1, collected_at: 1 });
target.review_decisions.createIndex({ claim_id: 1, created_at: 1 });
target.reports.createIndex({ content_ids: 1, created_at: -1 });
target.analyses.createIndex({ content_id: 1, created_at: -1 });
target.kb_documents.createIndex({ source_type: 1, source_id: 1 }, { partialFilterExpression: { source_id: { $type: "string" } } });
target.kb_documents.createIndex({ content_hash: 1 }, { unique: true });
target.kb_documents.createIndex({ status: 1, created_at: -1 });
target.kb_chunks.createIndex({ doc_id: 1, chunk_index: 1 }, { unique: true });
target.kb_qa_pairs.createIndex({ doc_id: 1, qa_index: 1 });
target.kb_qa_pairs.createIndex({ status: 1, updated_at: -1 });

print(`datapp MongoDB schema ready: ${target.getName()}`);
print("Atlas Vector Search index is separate; see the commented definition below.");

/* Atlas UI / mongosh Atlas command:
target.kb_chunks.createSearchIndex({
  name: "kb_chunks_vector",
  type: "vectorSearch",
  definition: {
    fields: [
      { type: "vector", path: "embedding", numDimensions: 768, similarity: "cosine" },
      { type: "filter", path: "doc_id" },
      { type: "filter", path: "modality" }
    ]
  }
});
*/
