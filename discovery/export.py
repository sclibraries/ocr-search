"""Generate a bounded, collection-scoped metadata query."""


def sample_sql(collection_id=1335646, after_file_id=0, limit=100):
    if type(collection_id) is not int or collection_id <= 0 or type(after_file_id) is not int or after_file_id < 0:
        raise ValueError('invalid collection or cursor')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('sample limit must be 1–100')
    return f"""SELECT JSON_OBJECT(
 'file_id', f.fid, 'filename', f.filename, 'uri', f.uri,
 'mime', f.filemime, 'recorded_bytes', f.filesize,
 'exported_at', UTC_TIMESTAMP(), 'collection_id', {collection_id},
 'media_ids', GROUP_CONCAT(DISTINCT mf.entity_id),
 'page_ids', GROUP_CONCAT(DISTINCT mo.field_media_of_target_id),
 'item_ids', GROUP_CONCAT(DISTINCT issue.entity_id),
 'campus_ids', CONCAT_WS(',', GROUP_CONCAT(DISTINCT campus.field_campus_target_id),
   GROUP_CONCAT(DISTINCT pagecampus.field_campus_target_id),
   GROUP_CONCAT(DISTINCT othercampus.field_campus_target_id)),
 'media_access_terms', GROUP_CONCAT(DISTINCT ma.field_access_terms_target_id),
 'page_access_terms', GROUP_CONCAT(DISTINCT pa.field_access_terms_target_id),
 'item_access_terms', GROUP_CONCAT(DISTINCT ia.field_access_terms_target_id),
 'mapping_scope', 'collection-to-issue-to-page; other file associations not exhausted',
 'access_status', 'unreviewed'
) AS candidate
FROM node__field_member_of issue
JOIN node__field_campus campus ON campus.entity_id=issue.entity_id
 AND campus.deleted=0 AND campus.field_campus_target_id=169
JOIN node__field_member_of page ON page.field_member_of_target_id=issue.entity_id AND page.deleted=0
JOIN media__field_media_of mo ON mo.field_media_of_target_id=page.entity_id AND mo.deleted=0
JOIN media__field_media_use mu ON mu.entity_id=mo.entity_id AND mu.deleted=0 AND mu.field_media_use_target_id=3507
JOIN media__field_media_file mf ON mf.entity_id=mo.entity_id AND mf.deleted=0
JOIN file_managed f ON f.fid=mf.field_media_file_target_id
LEFT JOIN node__field_campus pagecampus ON pagecampus.entity_id=page.entity_id AND pagecampus.deleted=0
LEFT JOIN node__field_member_of otherparent ON otherparent.entity_id=page.entity_id AND otherparent.deleted=0
LEFT JOIN node__field_campus othercampus ON othercampus.entity_id=otherparent.field_member_of_target_id AND othercampus.deleted=0
LEFT JOIN media__field_access_terms ma ON ma.entity_id=mf.entity_id AND ma.deleted=0
LEFT JOIN node__field_access_terms pa ON pa.entity_id=page.entity_id AND pa.deleted=0
LEFT JOIN node__field_access_terms ia ON ia.entity_id=issue.entity_id AND ia.deleted=0
WHERE issue.deleted=0 AND issue.field_member_of_target_id={collection_id} AND f.fid>{after_file_id}
GROUP BY f.fid, f.filename, f.uri, f.filemime, f.filesize
ORDER BY f.fid LIMIT {limit};"""
