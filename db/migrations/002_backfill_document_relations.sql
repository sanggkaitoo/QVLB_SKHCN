INSERT INTO document_relations (
    source_document_id,
    target_document_id,
    target_ref_text,
    normalized_target_ref,
    relation_type,
    confidence,
    verified
)
SELECT
    child_id,
    parent_id,
    parent_ref_text,
    upper(regexp_replace(translate(parent_so_ky, 'đĐ', 'dD'), '\s+', '', 'g')),
    'can_cu',
    CASE WHEN parent_id IS NOT NULL THEN 0.95 ELSE 0.6 END,
    parent_id IS NOT NULL
FROM can_cu
WHERE parent_ref_text IS NOT NULL
ON CONFLICT (source_document_id, relation_type, target_ref_text) DO UPDATE
SET target_document_id = EXCLUDED.target_document_id,
    normalized_target_ref = EXCLUDED.normalized_target_ref,
    confidence = GREATEST(document_relations.confidence, EXCLUDED.confidence),
    verified = document_relations.verified OR EXCLUDED.verified;
