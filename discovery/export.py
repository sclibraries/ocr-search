"""Generate a bounded, read-only Drupal relationship query."""


EXPORT_SCHEMA_VERSION = 1


def sample_sql(collection_id, hocr_media_use_id, after_file_id=0, limit=100,
               after_page_id=None, after_item_id=None, explain=False):
    """Return one JSON object per hOCR file/page association.

    Site-specific values are arguments instead of repository defaults. Canvas IDs,
    manifest URLs and ArchivesSpace links are intentionally left for the manifest
    and evidence join in :mod:`discovery.inventory`; Drupal node IDs are not canvas
    IDs.
    """
    if type(collection_id) is not int or collection_id <= 0:
        raise ValueError('collection ID must be a positive integer')
    if type(hocr_media_use_id) is not int or hocr_media_use_id <= 0:
        raise ValueError('hOCR media-use ID must be a positive integer')
    if type(after_file_id) is not int or after_file_id < 0:
        raise ValueError('file cursor must be a nonnegative integer')
    if (after_page_id is None) != (after_item_id is None):
        raise ValueError('page and item cursors must be supplied together')
    if after_page_id is not None and (type(after_page_id) is not int or after_page_id < 0):
        raise ValueError('page cursor must be a nonnegative integer')
    if after_item_id is not None and (type(after_item_id) is not int or after_item_id < 0):
        raise ValueError('item cursor must be a nonnegative integer')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('export limit must be 1–1000')

    cursor_clause = f'f.fid>{after_file_id}'
    if after_page_id is not None:
        cursor_clause = (
            f'({cursor_clause} OR (f.fid={after_file_id} AND page.entity_id>{after_page_id}) '
            f'OR (f.fid={after_file_id} AND page.entity_id={after_page_id} '
            f'AND issue.entity_id>{after_item_id}))'
        )

    query = f"""WITH RECURSIVE subtree(node_id, depth, path) AS (
 SELECT {collection_id}, 0, CAST({collection_id} AS CHAR(4000))
 UNION ALL
 SELECT relation.entity_id, subtree.depth+1,
   CONCAT(subtree.path, '>', relation.entity_id)
 FROM subtree
 JOIN node__field_member_of relation
   ON relation.field_member_of_target_id=subtree.node_id AND relation.deleted=0
 WHERE subtree.depth < 32
   AND LOCATE(CONCAT('>', relation.entity_id, '>'), CONCAT('>', subtree.path, '>'))=0
),
page_paths AS (
 SELECT node_id, GROUP_CONCAT(DISTINCT path) AS paths,
   MAX(depth) AS max_depth, MAX(depth >= 32) AS depth_limit_reached
 FROM subtree GROUP BY node_id
),
candidates AS (
SELECT JSON_OBJECT(
 'schema_version', {EXPORT_SCHEMA_VERSION},
 'file_id', f.fid,
 'filename', f.filename,
 'source_uri', f.uri,
 'exported_at', UTC_TIMESTAMP(),
 'collection_id', {collection_id},
 's3_key', CASE
   WHEN f.uri LIKE 'private://%' THEN REPLACE(f.uri, 'private://', 's3fs-private/')
   WHEN f.uri LIKE 'public://%' THEN REPLACE(f.uri, 'public://', 's3fs-public/')
   ELSE NULL END,
 'recorded_bytes', f.filesize,
 'page_id', page.entity_id,
 'item_id', issue.entity_id,
 'page_order', MAX(page_order.field_weight_value),
 'canvas_id', NULL,
 'manifest_url', NULL,
 'media_use', GROUP_CONCAT(DISTINCT mu.field_media_use_target_id),
 'campus_ids', GROUP_CONCAT(DISTINCT ancestry_campus.field_campus_target_id),
 'collection_ancestry', page_paths.paths,
 'ancestry_depth_limit_reached', page_paths.depth_limit_reached,
 'aspace_record', NULL,
 'publication_state', CASE
   WHEN MIN(page_state.status)=0 OR MIN(item_state.status)=0
     OR MIN(ancestor_state.status)=0 THEN 'unpublished'
   WHEN MIN(page_state.status)=1 AND MIN(item_state.status)=1
     AND MIN(ancestor_state.status)=1 THEN 'published'
   ELSE 'unknown' END,
 'access_terms', CONCAT_WS('|',
   GROUP_CONCAT(DISTINCT media_access.name),
   GROUP_CONCAT(DISTINCT ancestry_access.name)),
 'access_term_ids', CONCAT_WS(',',
   GROUP_CONCAT(DISTINCT media_terms.field_access_terms_target_id),
   GROUP_CONCAT(DISTINCT ancestry_terms.field_access_terms_target_id))
) AS candidate
FROM node__field_member_of issue
JOIN page_paths issue_paths ON issue_paths.node_id=issue.entity_id
JOIN node__field_member_of page
  ON page.field_member_of_target_id=issue.entity_id AND page.deleted=0
JOIN page_paths ON page_paths.node_id=page.entity_id
JOIN media__field_media_of media_page
  ON media_page.field_media_of_target_id=page.entity_id AND media_page.deleted=0
JOIN media__field_media_use mu
  ON mu.entity_id=media_page.entity_id AND mu.deleted=0
 AND mu.field_media_use_target_id={hocr_media_use_id}
JOIN media__field_media_file media_file
  ON media_file.entity_id=media_page.entity_id AND media_file.deleted=0
JOIN file_managed f ON f.fid=media_file.field_media_file_target_id
LEFT JOIN node__field_weight page_order
  ON page_order.entity_id=page.entity_id AND page_order.deleted=0
LEFT JOIN node_field_data page_state ON page_state.nid=page.entity_id
LEFT JOIN node_field_data item_state ON item_state.nid=issue.entity_id
LEFT JOIN node_field_data ancestor_state
  ON LOCATE(CONCAT('>', ancestor_state.nid, '>'), CONCAT('>', page_paths.paths, '>')) > 0
LEFT JOIN node__field_campus ancestry_campus
  ON LOCATE(CONCAT('>', ancestry_campus.entity_id, '>'), CONCAT('>', page_paths.paths, '>')) > 0
 AND ancestry_campus.deleted=0
LEFT JOIN media__field_access_terms media_terms
  ON media_terms.entity_id=media_file.entity_id AND media_terms.deleted=0
LEFT JOIN node__field_access_terms ancestry_terms
  ON LOCATE(CONCAT('>', ancestry_terms.entity_id, '>'), CONCAT('>', page_paths.paths, '>')) > 0
 AND ancestry_terms.deleted=0
LEFT JOIN taxonomy_term_field_data media_access
  ON media_access.tid=media_terms.field_access_terms_target_id
LEFT JOIN taxonomy_term_field_data ancestry_access
  ON ancestry_access.tid=ancestry_terms.field_access_terms_target_id
WHERE issue.deleted=0 AND {cursor_clause}
GROUP BY f.fid, f.filename, f.uri, f.filesize, page.entity_id, issue.entity_id,
  page_paths.paths, page_paths.depth_limit_reached
ORDER BY f.fid, page.entity_id, issue.entity_id LIMIT {limit}
)
SELECT candidate FROM candidates;"""
    return f'EXPLAIN FORMAT=JSON\n{query}' if explain else query
