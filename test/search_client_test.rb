require 'minitest/autorun'
require_relative '../lib/search_client'
class SearchClientTest < Minitest::Test
  def test_both_issues_omits_empty_filter
    query = URI.decode_www_form(OcrPilot::SearchClient.uri('http://localhost/api/ocr/search', 'mascot', '').query).to_h
    assert_equal 'mascot', query['q']
    refute query.key?('item')
  end
  def test_pagination_is_forwarded
    query = URI.decode_www_form(OcrPilot::SearchClient.uri('http://localhost/api/ocr/search', 'the', '', '2').query).to_h
    assert_equal '2', query['page']
  end
  def test_phrase_and_item_are_encoded
    query = URI.decode_www_form(OcrPilot::SearchClient.uri('http://localhost/api/ocr/search', '"Eleanor Kearns"', 'scw').query).to_h
    assert_equal '"Eleanor Kearns"', query['q']
    assert_equal 'scw', query['item']
    assert_raises(IOError) { OcrPilot::SearchClient.uri('', 'mascot', '') }
  end
end
