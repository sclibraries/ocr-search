require 'minitest/autorun'
require_relative '../lib/search_client'

class SearchClientTest < Minitest::Test
  def test_search_uri_sends_only_supported_v2_parameters
    uri = OcrPilot::SearchClient.uri('http://localhost/api/ocr/search', {
      'q' => 'mascot', 'series' => 'Example Weekly', 'year_from' => '1920',
      'year_to' => '1929', 'collection' => '/repositories/2/resources/1', 'sort' => 'date_desc',
      'page' => '2', 'per_page' => '10', 'ignored' => 'discard me'
    })

    assert_equal 'http', uri.scheme
    assert_equal 'localhost', uri.host
    assert_equal '/api/ocr/search', uri.path
    assert_equal({
      'q' => 'mascot', 'series' => 'Example Weekly', 'year_from' => '1920',
      'year_to' => '1929', 'collection' => '/repositories/2/resources/1', 'sort' => 'date_desc',
      'page' => '2', 'per_page' => '10'
    }, URI.decode_www_form(uri.query).to_h)
  end

  def test_item_uri_targets_the_v2_item_endpoint_after_validating_id
    uri = OcrPilot::SearchClient.item_uri('http://localhost/api/ocr/search', 'demo-3')

    assert_equal '/api/ocr/items/demo-3', uri.path
    assert_nil uri.query
    assert_raises(ArgumentError) { OcrPilot::SearchClient.item_uri('http://localhost/api/ocr/search', '../private') }
  end

  def test_search_query_and_item_are_encoded
    uri = OcrPilot::SearchClient.uri('http://localhost/api/ocr/search', {'q' => '"two words"', 'item' => 'demo-3'})
    query = URI.decode_www_form(uri.query).to_h

    assert_equal '"two words"', query['q']
    assert_equal 'demo-3', query['item']
    assert_raises(IOError) { OcrPilot::SearchClient.uri('', {'q' => 'mascot'}) }
  end
end
