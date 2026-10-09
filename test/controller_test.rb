require 'json'
require 'minitest/autorun'
require 'minitest/mock'
require_relative 'support/controller_harness'
require_relative '../public/controllers/ocr_search_controller'

class OcrSearchControllerTest < Minitest::Test
  Response = Struct.new(:code, :body)
  API = 'http://fake-api.test/api/ocr/search'

  def test_view_context_exposes_declared_helpers_but_not_controller_actions
    controller = OcrSearchController.new(q: 'mascot')
    controller.instance_variable_set(:@query, 'mascot')
    controller.instance_variable_set(:@filters, {})

    assert_equal '/digital-text/items/demo-a?q=mascot',
                 controller.render_view_source('<%= item_page_url("demo-a") %>')
    assert_raises(NoMethodError) do
      controller.render_view_source('<%= self.index %>')
    end
  end

  def test_url_helpers_use_app_prefix_from_the_view_helpers
    controller = OcrSearchController.new(q: 'mascot')
    controller.instance_variable_set(:@query, 'mascot')
    controller.instance_variable_set(:@filters, {})
    controller.instance_variable_set(:@sort, 'relevance')
    controller.instance_variable_set(:@per_page, 10)

    assert_equal '/digital-text?q=mascot&sort=relevance', controller.search_url
    assert_equal '/digital-text/items/demo-a?q=mascot', controller.item_page_url('demo-a')
    refute_respond_to controller, :app_prefix
  end

  def test_filters_chips_sort_and_grouped_results_render_from_v2_response
    api = FakeAPI.new(search: Response.new('200', JSON.generate(search_payload)))

    with_api(api) do
      controller = OcrSearchController.new(
        q: 'mascot', series: 'Example Weekly', year_from: '1920', year_to: '1929', sort: 'date_desc'
      )
      controller.index
      html = controller.render_view('index')

      assert_equal({
        'q' => 'mascot', 'series' => 'Example Weekly', 'year_from' => '1920',
        'year_to' => '1929', 'sort' => 'date_desc', 'page' => '1'
      }, api.search_requests.fetch(0).fetch(:params))
      assert_includes html, 'Publication'
      assert_includes html, 'Date'
      assert_includes html, 'Example Weekly (2 items)'
      assert_includes html, '1920s (2 items)'
      assert_includes html, 'aria-label="Remove Publication filter: Example Weekly"'
      assert_includes html, 'aria-label="Remove start year filter: 1920"'
      assert_includes html, 'Clear all'
      assert_includes html, 'Newest first'
      assert_includes html, '2 matching items'
      assert_includes html, '3 matching pages'
      assert_equal 2, html.scan('class="ocr-result"').length
      assert_includes html, 'A <mark>mascot</mark> appears.'
      assert_includes html, 'See all 2 matching pages'
      assert_includes html, '<nav aria-label="Filter by publication">'
      assert_includes html, '<nav aria-label="Filter by decade">'
      assert_includes html, '<legend>Filter by year range</legend>'
      assert_match(/Newest first<\/a>\s*<\/li>/m, html)
      assert_match(/aria-current="true">Newest first/m, html)
      assert_includes html, 'href="/digital-text?q=mascot&amp;sort=relevance" class="ocr-clear-filters"'
      refute_match(/ocr-result-count[^>]*aria-live/, html)
      refute_includes html, 'Search within'
    end
  end

  def test_item_scope_uses_api_pattern_and_shows_page_count
    payload = search_payload
    payload['filters']['item'] = 'demo-a'
    payload['results'] = [payload['results'].first]
    payload['page'] = 2
    payload['per_page'] = 25
    payload['total_items'] = 1
    api = FakeAPI.new(search: Response.new('200', JSON.generate(payload)))

    with_api(api) do
      controller = OcrSearchController.new(q: 'mascot', item: 'demo-a', page: '2', per_page: '25', sort: 'date_asc')
      controller.index
      html = controller.render_view('index')

      assert_equal 'demo-a', api.search_requests.fetch(0).fetch(:params)['item']
      assert_equal 'date_asc', api.search_requests.fetch(0).fetch(:params)['sort']
      assert_equal '2', api.search_requests.fetch(0).fetch(:params)['page']
      assert_includes html, 'Item: Example Weekly, 1927-12-07 ×'
      assert_includes html, 'aria-label="Remove item filter: Example Weekly, 1927-12-07"'
      assert_equal '25', api.search_requests.fetch(0).fetch(:params)['per_page']
      assert_equal 1, html.scan('class="ocr-result"').length
    end
  end

  def test_grouped_results_never_render_more_than_three_pages_or_two_snippets_per_page
    payload = search_payload
    payload['results'] = [payload['results'].first]
    payload['results'][0]['matching_pages'] = 4
    payload['results'][0]['pages'] = (1..4).map do |number|
      page = page_payload('demo-a', number)
      page['snippets'] = 3.times.map do |index|
        {'html' => "Synthetic excerpt #{index + 1}.", 'pages' => [], 'regions' => [], 'highlights' => []}
      end
      page
    end
    payload['total_items'] = 1
    payload['matching_pages'] = 4
    api = FakeAPI.new(search: Response.new('200', JSON.generate(payload)))

    with_api(api) do
      controller = OcrSearchController.new(q: 'mascot')
      controller.index
      html = controller.render_view('index')

      assert_equal 3, html.scan('class="ocr-page-excerpt"').length
      assert_equal 6, html.scan('class="ocr-excerpt"').length
      assert_includes html, 'See all 4 matching pages'
      refute_includes html, 'Synthetic excerpt 3.'
    end
  end

  def test_non_pilot_item_page_uses_api_metadata_and_excerpts_without_a_viewer
    details = {
      'item_id' => 'demo-c', 'title' => 'Synthetic issue C', 'series' => 'Example Review',
      'issue_date' => '1931-04', 'collection_id' => '/repositories/2/resources/1',
      'aspace_record' => 'https://example.org/records/demo-c',
      'source_manifest_url' => 'https://example.org/manifests/demo-c',
      'page_count' => 2, 'truncated' => false,
      'pages' => [
        {'page_number' => 1, 'page_label' => 'Page 1', 'canvas_id' => 'https://example.org/canvas/demo-c/1'},
        {'page_number' => 2, 'page_label' => 'Page 2', 'canvas_id' => 'https://example.org/canvas/demo-c/2'}
      ]
    }
    payload = item_search_payload('demo-c', details['pages'].first['canvas_id'])
    api = FakeAPI.new(search: Response.new('200', JSON.generate(payload)), item: Response.new('200', JSON.generate(details)))

    with_api(api) do
      controller = OcrSearchController.new(item: 'demo-c', canvas: details['pages'].first['canvas_id'], q: 'mascot')
      controller.show
      html = controller.render_view('show')

      assert_equal ['demo-c'], api.item_requests.map { |request| request.fetch(:id) }
      assert_equal 'demo-c', api.search_requests.fetch(0).fetch(:params)['item']
      assert_includes html, 'Synthetic issue C'
      assert_includes html, 'Example Review'
      assert_includes html, 'Pages in this item'
      assert_includes html, 'Page 2'
      assert_includes html, 'A <mark>mascot</mark> appears on the first page.'
      assert_includes html, 'View in the finding aid'
      assert_includes html, 'https://example.org/records/demo-c'
      refute_includes html, 'data-dv-source-group'
      refute_includes html, 'Open page 1 image'
    end
  end

  def test_catalog_item_page_keeps_the_pilot_viewer_and_highlight_controls
    item_id = OcrPilot::Catalog::DATA.keys.first
    catalog_page = OcrPilot::Catalog.item(item_id).fetch('pages').first
    details = {
      'item_id' => item_id, 'title' => 'Synthetic pilot item', 'series' => 'Example Weekly',
      'issue_date' => '1927-12-07', 'collection_id' => '/repositories/2/resources/1',
      'aspace_record' => 'https://example.org/records/demo-pilot',
      'source_manifest_url' => 'https://example.org/manifests/demo-pilot',
      'page_count' => 1, 'truncated' => false,
      'pages' => [{'page_number' => catalog_page['number'], 'page_label' => catalog_page['label'],
                   'canvas_id' => catalog_page['canvas']}]
    }
    matches = item_search_payload('demo-c', catalog_page['canvas'])
    matches['results'][0]['item_id'] = item_id
    matches['results'][0]['pages'][0]['canvas_id'] = catalog_page['canvas']
    matches['results'][0]['pages'][0]['snippets'][0]['html'] = 'Synthetic <mark>highlight</mark>.'
    api = FakeAPI.new(search: Response.new('200', JSON.generate(matches)), item: Response.new('200', JSON.generate(details)))

    with_api(api) do
      controller = OcrSearchController.new(item: item_id, canvas: catalog_page['canvas'], q: 'highlight')
      controller.show
      html = controller.render_view('show')

      assert_includes html, 'data-dv-source-group="ocr-page"'
      assert_includes html, 'data-ocr-highlights='
      assert_includes html, 'data-ocr-next'
      assert_includes html, 'Previous match'
      assert_includes html, 'Synthetic <mark>highlight</mark>.'
    end
  end

  def test_bad_item_identifier_is_rejected_before_calling_the_api
    api = FakeAPI.new

    with_api(api) do
      controller = OcrSearchController.new(item: '../private', canvas: 'anything')
      controller.show
      html = controller.render_view('show')

      assert_equal 404, controller.rendered_status
      assert_empty api.item_requests
      refute_includes html, 'data-dv-source-group'
    end
  end

  def test_index_checks_item_pattern_even_before_a_query_is_entered
    api = FakeAPI.new

    with_api(api) do
      controller = OcrSearchController.new(item: '../private')
      controller.index
      html = controller.render_view('index')

      assert_empty api.search_requests
      assert_includes html, 'role="alert"'
      assert_includes html, 'Please choose valid filters'
      refute_includes html, '../private'
    end
  end

  def test_search_rate_limit_unavailable_and_bad_request_keep_the_search_form
    {
      '429' => 'Search is busy; try again in a moment',
      '503' => 'Text search is temporarily unavailable',
      '400' => 'Please use words or a phrase'
    }.each do |code, message|
      api = FakeAPI.new(search: Response.new(code, '{"error":"busy"}'))
      with_api(api) do
        controller = OcrSearchController.new(q: 'mascot')
        controller.index
        html = controller.render_view('index')

        assert_includes html, message
        assert_includes html, 'role="alert"'
        assert_includes html, 'name="q"'
        assert_includes html, 'Search text'
      end
    end
  end

  def test_item_page_reports_api_errors_without_losing_the_finding_aid_search_path
    item = {
      'item_id' => 'demo-c', 'title' => 'Synthetic issue C', 'series' => 'Example Review',
      'issue_date' => '1931-04', 'collection_id' => '/repositories/2/resources/1',
      'aspace_record' => 'https://example.org/records/demo-c',
      'source_manifest_url' => 'https://example.org/manifests/demo-c',
      'page_count' => 0, 'truncated' => false, 'pages' => []
    }
    {
      '429' => 'Search is busy; try again in a moment',
      '503' => 'Text search is temporarily unavailable',
      '400' => 'Please use words or a phrase'
    }.each do |code, message|
      [[:item, Response.new(code, '{"error":"busy"}')],
       [:search, Response.new(code, '{"error":"busy"}')]].each do |endpoint, error_response|
        api = FakeAPI.new(
          item: endpoint == :item ? error_response : Response.new('200', JSON.generate(item)),
          search: endpoint == :search ? error_response : Response.new('200', JSON.generate(item_search_payload('demo-c', 'https://example.org/canvas/demo-c/1')))
        )
        with_api(api) do
          controller = OcrSearchController.new(item: 'demo-c', q: 'mascot')
          controller.show
          html = controller.render_view('show')

          assert_includes html, message
          assert_includes html, 'role="alert"'
          if endpoint == :search
            assert_includes html, 'Synthetic issue C'
            assert_includes html, 'Search this item'
          else
            assert_includes html, 'Back to text search'
          end
        end
      end
    end
  end

  private

  def with_api(api, &block)
    get_search = ->(endpoint, params) { api.search(endpoint, params) }
    get_item = ->(endpoint, id) { api.item(endpoint, id) }
    OcrPilot::SearchClient.stub(:get, get_search) do
      OcrPilot::SearchClient.stub(:get_item, get_item, &block)
    end
  end

  def search_payload
    {
      'query' => 'mascot', 'corpus' => 'example', 'sort' => 'date_desc',
      'filters' => {'series' => 'Example Weekly', 'year_from' => 1920, 'year_to' => 1929,
                    'collection' => nil, 'item' => nil},
      'page' => 1, 'per_page' => 10, 'total_items' => 2, 'matching_pages' => 3,
      'partial' => false, 'solr_time_ms' => 4,
      'facets' => {
        'series' => [{'value' => 'Example Weekly', 'count' => 2}, {'value' => 'Other Weekly', 'count' => 1}],
        'decade' => [{'value' => 1920, 'count' => 2}, {'value' => 1930, 'count' => 1}]
      },
      'results' => [
        {
          'item_id' => 'demo-a', 'title' => 'Example Weekly, 1927-12-07', 'series' => 'Example Weekly',
          'issue_date' => '1927-12-07', 'collection_id' => '/repositories/2/resources/1',
          'aspace_record' => 'https://example.org/records/demo-a',
          'source_manifest_url' => 'https://example.org/manifests/demo-a', 'matching_pages' => 2,
          'pages' => [page_payload('demo-a', 8), page_payload('demo-a', 10)]
        },
        {
          'item_id' => 'demo-b', 'title' => 'Other Weekly, 1931-04', 'series' => 'Other Weekly',
          'issue_date' => '1931-04', 'collection_id' => '/repositories/2/resources/1',
          'aspace_record' => 'https://example.org/records/demo-b',
          'source_manifest_url' => 'https://example.org/manifests/demo-b', 'matching_pages' => 1,
          'pages' => [page_payload('demo-b', 2)]
        }
      ]
    }
  end

  def page_payload(item_id, page_number)
    {
      'id' => "example:#{item_id}:#{page_number}", 'page_number' => page_number,
      'page_label' => "Page #{page_number}", 'canvas_id' => "https://example.org/canvas/#{item_id}/#{page_number}",
      'snippets' => [{'html' => 'A <mark>mascot</mark> appears.', 'pages' => [], 'regions' => [], 'highlights' => []}]
    }
  end

  def item_search_payload(item_id, canvas_id)
    {
      'query' => 'mascot', 'corpus' => 'example', 'sort' => 'relevance',
      'filters' => {'series' => nil, 'year_from' => nil, 'year_to' => nil,
                    'collection' => nil, 'item' => item_id},
      'page' => 1, 'per_page' => 10, 'total_items' => 1, 'matching_pages' => 1,
      'partial' => false, 'solr_time_ms' => 3,
      'facets' => {'series' => [], 'decade' => []},
      'results' => [{
        'item_id' => item_id, 'title' => 'Synthetic issue C', 'series' => 'Example Review',
        'issue_date' => '1931-04', 'collection_id' => '/repositories/2/resources/1',
        'aspace_record' => 'https://example.org/records/demo-c',
        'source_manifest_url' => 'https://example.org/manifests/demo-c', 'matching_pages' => 1,
        'pages' => [{'id' => 'example:demo-c:1', 'page_number' => 1, 'page_label' => 'Page 1',
                     'canvas_id' => canvas_id,
                     'snippets' => [{'html' => 'A <mark>mascot</mark> appears on the first page.',
                                     'pages' => [], 'regions' => [], 'highlights' => []}]}]
      }]
    }
  end

  class FakeAPI
    attr_reader :search_requests, :item_requests

    def initialize(search: nil, item: nil)
      @search_response = search
      @item_response = item
      @search_requests = []
      @item_requests = []
    end

    def search(endpoint, params)
      @search_requests << {endpoint: endpoint, params: params}
      @search_response
    end

    def item(endpoint, id)
      @item_requests << {endpoint: endpoint, id: id}
      @item_response || Response.new('404', '{}')
    end
  end
end
