require 'json'
require 'digest'
require 'fileutils'
require 'minitest/autorun'
require 'minitest/mock'
require 'tmpdir'
require_relative 'support/controller_harness'
require_relative '../public/controllers/ocr_search_controller'
require_relative '../public/asset_versions'

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

  def test_layout_head_asset_urls_use_stable_content_versions
    asset_names = %w[ocr_search.css ocr_highlights.css ocr_highlights.js]
    asset_directory = File.expand_path('../public/assets', __dir__)
    expected_versions = asset_names.to_h do |name|
      [name, Digest::SHA256.file(File.join(asset_directory, name)).hexdigest[0, 12]]
    end

    html = PluginHeadView.new.render_layout_head
    expected_versions.each do |name, version|
      assert_includes html, "/archives/assets/#{name}?v=#{version}"
    end
    assert_equal html, PluginHeadView.new.render_layout_head

    assert_equal expected_versions, OcrSearchAssetVersions::VERSIONS

    Dir.mktmpdir do |directory|
      asset_names.each do |name|
        FileUtils.cp(File.join(asset_directory, name), File.join(directory, name))
      end

      original_versions = OcrSearchAssetVersions.calculate(directory)
      assert_equal expected_versions, original_versions
      assert_equal original_versions, OcrSearchAssetVersions.calculate(directory)

      File.open(File.join(directory, 'ocr_highlights.css'), 'ab') { |file| file.write("\n") }
      updated_versions = OcrSearchAssetVersions.calculate(directory)
      refute_equal original_versions['ocr_highlights.css'], updated_versions['ocr_highlights.css']
      %w[ocr_search.css ocr_highlights.js].each do |name|
        assert_equal original_versions[name], updated_versions[name]
      end
    end
  end

  def test_filters_chips_sort_and_grouped_results_render_from_v2_response
    payload = search_payload
    payload['results'][0]['pages'][0]['snippets'][0]['html'] = 'A <mark>mascot</mark> appears....'
    api = FakeAPI.new(search: Response.new('200', JSON.generate(payload)))

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
      active_filters_position = html.index('<section class="ocr-active-filters"')
      clear_all_position = html.index('>Clear all</a>')
      publication_filter_position = html.index('<nav aria-label="Filter by publication">')
      assert_operator active_filters_position, :<, publication_filter_position
      assert_operator clear_all_position, :<, publication_filter_position
      assert_includes html, 'Newest first'
      assert_includes html, '2 matching items'
      assert_includes html, '3 matching pages'
      assert_equal 2, html.scan('class="ocr-result"').length
      assert_includes html, '…A <mark>mascot</mark> appears…'
      refute_includes html, 'appears.…'
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

  def test_active_filter_summary_is_omitted_when_no_filters_are_set
    payload = search_payload
    payload['filters'] = {'series' => nil, 'year_from' => nil, 'year_to' => nil,
                          'collection' => nil, 'item' => nil}
    api = FakeAPI.new(search: Response.new('200', JSON.generate(payload)))

    with_api(api) do
      controller = OcrSearchController.new(q: 'dance')
      controller.index
      html = controller.render_view('index')

      refute_includes html, '<section class="ocr-active-filters"'
      refute_includes html, 'Clear all'
    end
  end

  def test_single_matching_page_uses_singular_view_link_wording
    payload = search_payload
    payload['total_items'] = 1
    payload['matching_pages'] = 1
    payload['results'] = [payload['results'].first]
    payload['results'][0]['matching_pages'] = 1
    payload['results'][0]['pages'] = [payload['results'][0]['pages'].first]
    api = FakeAPI.new(search: Response.new('200', JSON.generate(payload)))

    with_api(api) do
      controller = OcrSearchController.new(q: 'mascot')
      controller.index
      html = controller.render_view('index')

      assert_includes html, 'See the matching page'
      refute_includes html, 'See all 1 matching pages'
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
      'page_count' => 2, 'truncated' => false, 'pages_page' => 1,
      'pages_per_page' => 50, 'pages_total_pages' => 1,
      'selected' => {'position' => 1, 'page_number' => 1, 'page_label' => 'Page 1',
                     'canvas_id' => 'https://example.org/canvas/demo-c/1', 'previous' => nil,
                     'next' => {'page_number' => 2, 'page_label' => 'Page 2',
                                'canvas_id' => 'https://example.org/canvas/demo-c/2'}},
      'pages' => [
        {'page_number' => 1, 'page_label' => 'Page 1', 'canvas_id' => 'https://example.org/canvas/demo-c/1'},
        {'page_number' => 2, 'page_label' => 'Page 2', 'canvas_id' => 'https://example.org/canvas/demo-c/2'}
      ]
    }
    payload = item_search_payload('demo-c', details['pages'].first['canvas_id'])
    payload['results'][0]['pages'][0]['snippets'][0]['html'] = 'A <mark>mascot</mark> appears on the first page....'
    api = FakeAPI.new(search: Response.new('200', JSON.generate(payload)), item: Response.new('200', JSON.generate(details)))

    with_api(api) do
      controller = OcrSearchController.new(item: 'demo-c', canvas: details['pages'].first['canvas_id'], q: 'mascot')
      controller.show
      html = controller.render_view('show')

      assert_equal ['demo-c'], api.item_requests.map { |request| request.fetch(:id) }
      assert_equal 'demo-c', api.search_requests.fetch(0).fetch(:params)['item']
      assert_includes html, 'Synthetic issue C'
      assert_includes html, 'Example Review'
      assert_includes html, 'Page 1 of 2'
      assert_includes html, 'Page 2 ›'
      assert_includes html, 'Back to page list'
      assert_includes html, '…A <mark>mascot</mark> appears on the first page…'
      refute_includes html, 'first page.…'
      assert_includes html, 'View in the finding aid'
      assert_includes html, 'https://example.org/records/demo-c'
      refute_includes html, 'data-dv-source-group'
      refute_includes html, 'Open page 1 image'
    end
  end

  def test_long_item_renders_only_the_requested_first_middle_or_last_50_page_slice
    api = synthetic_long_item_api

    {'1' => 'Pages 1–50 of 1200', '12' => 'Pages 551–600 of 1200',
     '24' => 'Pages 1151–1200 of 1200'}.each do |pages_page, range|
      with_api(api) do
        controller = OcrSearchController.new(
          item: 'demo-long', pages_page: pages_page,
          q: 'mascot', series: 'Example Weekly', year_from: '1920', collection: '/repositories/2/resources/1'
        )
        controller.show
        html = controller.render_view('show')
        request = api.item_requests.last

        assert_equal({'pages_page' => pages_page, 'pages_per_page' => '50'}, request.fetch(:params))
        assert_includes html, range
        assert_operator html.scan(/<li><a /).length, :<=, 50
        assert_operator html.scan(/<a\b/).length, :<=, 100
        assert_includes html, 'aria-label="Pages in this item"'
        refute_includes html, 'first 1,000 pages'
        if pages_page == '1'
          refute_match(/<a[^>]*>‹ Previous<\/a>/, html)
        elsif pages_page == '24'
          refute_match(/<a[^>]*>Next ›<\/a>/, html)
        end
        assert_internal_links_keep_state(html, {
          'q' => 'mascot', 'series' => 'Example Weekly', 'year_from' => '1920',
          'collection' => '/repositories/2/resources/1'
        })
      end
    end
  end

  def test_page_tiles_show_numbers_with_full_labels_for_accessibility
    api = synthetic_long_item_api

    with_api(api) do
      controller = OcrSearchController.new(item: 'demo-long', pages_page: '1')
      controller.show
      html = controller.render_view('show')

      assert_match(/<a href="[^"]+" aria-label="Sophian, Volume 51, Number 14, Page 1" title="Sophian, Volume 51, Number 14, Page 1">1<\/a>/, html)
      assert_match(/<a href="[^"]+" aria-label="Page 2" title="Page 2">2<\/a>/, html)
      refute_includes html, '>Sophian, Volume 51, Number 14, Page 1</a>'
    end
  end

  def test_page_number_jumps_redirect_from_item_and_page_views_to_selected_page
    api = synthetic_long_item_api
    [
      [{item: 'demo-long', page_number: '1150', q: 'mascot', series: 'Example Weekly',
        year_from: '1920', year_to: '1929', collection: '/repositories/2/resources/1',
        sort: 'date_desc', page: '3'}, '1150', nil],
      [{item: 'demo-long', page_number: '571', q: 'mascot'}, '572', nil],
      [{item: 'demo-long', page_number: '1300', q: 'mascot', series: 'Example Weekly'}, '1201', '1300']
    ].each do |(input, target_number, missed_number)|
      with_api(api) do
        controller = OcrSearchController.new(input)
        controller.show
        request = api.item_requests.last
        location = URI(controller.redirect_location)
        query = URI.decode_www_form(location.query).to_h

        assert_equal 302, controller.rendered_status
        assert_equal({'page_number' => input[:page_number], 'pages_per_page' => '50'}, request.fetch(:params))
        assert_equal '/digital-text/items/demo-long', location.path
        assert_equal "https://example.org/canvas/demo-long/#{target_number}", query['canvas']
        assert_equal input[:q], query['q']
        %w[series year_from year_to collection sort page].each do |key|
          assert_equal input[key], query[key] if input[key]
        end
        assert_equal '25', query['per_page'] if input[:page]
        missed_number ? assert_equal(missed_number, query['missed']) : assert_nil(query['missed'])
        refute_includes query, 'page_number'

        if missed_number
          page_controller = OcrSearchController.new(item: 'demo-long', canvas: query['canvas'], missed: query['missed'])
          page_controller.show
          page_html = page_controller.render_view('show')
          assert_includes page_html, 'There is no page 1300; showing page 1201.'
        end
      end
    end
  end

  def test_invalid_page_number_uses_the_existing_bad_request_response
    %w[0 nope 2147483648].each do |page_number|
      api = synthetic_long_item_api

      with_api(api) do
        controller = OcrSearchController.new(item: 'demo-long', page_number: page_number)
        controller.show

        assert_equal 400, controller.rendered_status
        assert_nil controller.redirect_location
        assert_empty api.item_requests
      end
    end
  end

  def test_invalid_missed_parameter_does_not_show_a_miss_notice
    api = synthetic_long_item_api

    with_api(api) do
      controller = OcrSearchController.new(
        item: 'demo-long', canvas: 'https://example.org/canvas/demo-long/37', missed: '0'
      )
      controller.show
      html = controller.render_view('show')

      assert_equal 200, controller.rendered_status
      refute_includes html, 'There is no page'
    end
  end

  def test_page_view_uses_selected_neighbors_and_hides_absent_neighbors_without_rendering_the_full_list
    api = synthetic_long_item_api
    {'1' => ['Page 1 of 1200', nil, 'Page 2'],
     '37' => ['Page 37 of 1200', 'Page 36', 'Page 38'],
     '572' => ['Page 572 (571 of 1200)', 'Page 570', 'Page 573'],
     '1201' => ['Page 1201 (1200 of 1200)', 'Page 1200', nil]}.each do |page_number, (current, previous, following)|
      with_api(api) do
        controller = OcrSearchController.new(item: 'demo-long', canvas: "https://example.org/canvas/demo-long/#{page_number}", q: 'mascot', year_to: '1929')
        controller.show
        html = controller.render_view('show')
        request = api.item_requests.last

        assert_equal({'canvas' => "https://example.org/canvas/demo-long/#{page_number}", 'pages_per_page' => '50'}, request.fetch(:params))
        assert_includes html, current
        previous ? assert_includes(html, previous) : refute_includes(html, 'Page 0')
        following ? assert_includes(html, following) : refute_includes(html, 'Page 1202')
        assert_includes html, 'Back to page list'
        assert_includes html, 'aria-current="page"'
        refute_includes html, 'class="ocr-pages"'
        refute_includes html, 'Pages in this item ('
        assert_operator html.scan(/<a\b/).length, :<=, 100
        assert_equal 1, html.scan(/aria-label="Page navigation"/).length
        assert_internal_links_keep_state(html, {'q' => 'mascot', 'year_to' => '1929'})
        back_href = html.match(/<a href="([^"]+)">Back to page list/)[1]
        back_query = URI.decode_www_form(URI(CGI.unescapeHTML(back_href)).query).to_h
        expected_set = {'572' => '12', '1201' => '24'}.fetch(page_number, '1')
        assert_equal expected_set, back_query['pages_page']
      end
    end
  end

  def test_item_jump_form_is_labelled_get_and_preserves_search_state
    api = synthetic_long_item_api

    with_api(api) do
      controller = OcrSearchController.new(
        item: 'demo-long', q: 'mascot', series: 'Example Weekly', year_from: '1920',
        year_to: '1929', collection: '/repositories/2/resources/1', sort: 'date_desc', page: '3'
      )
      controller.show
      html = controller.render_view('show')

      assert_match(/<form[^>]*method="get"[^>]*>/, html)
      assert_includes html, '<label for="ocr-page-number">Go to page</label>'
      assert_includes html, 'name="page_number"'
      %w[q series year_from year_to collection sort page per_page].each do |key|
        assert_includes html, %(name="#{key}" value=)
      end
    end
  end

  def test_page_jump_form_is_labelled_get_and_preserves_search_state
    api = synthetic_long_item_api

    with_api(api) do
      controller = OcrSearchController.new(
        item: 'demo-long', canvas: 'https://example.org/canvas/demo-long/37', q: 'mascot',
        series: 'Example Weekly', year_from: '1920', year_to: '1929',
        collection: '/repositories/2/resources/1', sort: 'date_desc', page: '3'
      )
      controller.show
      html = controller.render_view('show')

      assert_match(/<nav aria-label="Page navigation".*?<\/nav>\s*<form(?=[^>]*method="get")(?=[^>]*class="ocr-page-jump")[^>]*>/m, html)
      assert_includes html, '<label for="ocr-page-number">Go to page</label>'
      %w[q series year_from year_to collection sort page per_page].each do |key|
        assert_includes html, %(name="#{key}" value=)
      end
    end
  end

  def test_submitting_page_view_jump_form_redirects_directly_to_requested_page
    api = synthetic_long_item_api

    with_api(api) do
      page_controller = OcrSearchController.new(
        item: 'demo-long', canvas: 'https://example.org/canvas/demo-long/37', q: 'mascot',
        series: 'Example Weekly', year_from: '1920'
      )
      page_controller.show
      html = page_controller.render_view('show')
      hidden_parameters = html.scan(/<input type="hidden" name="([^"]+)" value="([^"]*)">/).to_h
      hidden_parameters.transform_values! { |value| CGI.unescapeHTML(value) }

      jump_controller = OcrSearchController.new(hidden_parameters.merge(item: 'demo-long', page_number: '1150'))
      jump_controller.show
      location = URI(jump_controller.redirect_location)
      query = URI.decode_www_form(location.query).to_h

      assert_equal 302, jump_controller.rendered_status
      assert_equal '/digital-text/items/demo-long', location.path
      refute_includes query, 'page_number'
      assert_equal 'mascot', query['q']
      assert_equal 'Example Weekly', query['series']
      assert_equal '1920', query['year_from']
      assert_equal 'https://example.org/canvas/demo-long/1150', query['canvas']
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
      'page_count' => 1, 'truncated' => false, 'pages_page' => 1,
      'pages_per_page' => 50, 'pages_total_pages' => 1,
      'selected' => {'position' => 1, 'page_number' => catalog_page['number'],
                     'page_label' => catalog_page['label'], 'canvas_id' => catalog_page['canvas'],
                     'previous' => nil, 'next' => nil},
      'pages' => [{'page_number' => catalog_page['number'], 'page_label' => catalog_page['label'],
                   'canvas_id' => catalog_page['canvas']}]
    }
    matches = item_search_payload('demo-c', catalog_page['canvas'])
    matches['results'][0]['item_id'] = item_id
    matches['results'][0]['pages'][0]['canvas_id'] = catalog_page['canvas']
    matches['results'][0]['pages'][0]['snippets'][0]['html'] = 'Synthetic <mark>highlight</mark>....'
    api = FakeAPI.new(search: Response.new('200', JSON.generate(matches)), item: Response.new('200', JSON.generate(details)))

    with_api(api) do
      controller = OcrSearchController.new(item: item_id, canvas: catalog_page['canvas'], q: 'highlight')
      controller.show
      html = controller.render_view('show')

      assert_includes html, 'data-dv-source-group="ocr-page"'
      assert_includes html, 'data-ocr-highlights='
      assert_includes html, 'data-ocr-next'
      assert_includes html, 'Previous match'
      assert_includes html, '…Synthetic <mark>highlight</mark>…'
      refute_includes html, 'highlight</mark>.…'
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
    get_item = ->(endpoint, id, params = {}) { api.item(endpoint, id, params) }
    OcrPilot::SearchClient.stub(:get, get_search) do
      OcrPilot::SearchClient.stub(:get_item, get_item, &block)
    end
  end

  def assert_internal_links_keep_state(html, expected)
    html.scan(/href="([^"]+)"/).flatten.each do |href|
      next unless href.start_with?('/digital-text')
      query = URI.decode_www_form(URI(CGI.unescapeHTML(href)).query || '').to_h
      expected.each { |key, value| assert_equal value, query[key] }
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

  def long_item_response(parameters)
    pages = (1..1200).map do |position|
      page_number = position >= 571 ? position + 1 : position
      page_label = if position == 1
                     'Sophian, Volume 51, Number 14, Page 1'
                   elsif position == 2
                     nil
                   else
                     "Page #{page_number}"
                   end
      {'page_number' => page_number, 'page_label' => page_label,
       'canvas_id' => "https://example.org/canvas/demo-long/#{page_number}"}
    end
    page_count = pages.length
    pages_per_page = parameters.fetch('pages_per_page', '50').to_i
    jump_missed = false
    selected_index = nil

    if parameters['canvas']
      selected_index = pages.index { |page| page['canvas_id'] == parameters['canvas'] }
      return Response.new('404', '{}') unless selected_index
      pages_page = selected_index / pages_per_page + 1
    elsif parameters['page_number']
      jump_position = pages.index { |page| page['page_number'] >= parameters['page_number'].to_i }
      jump_missed = jump_position.nil?
      jump_position ||= pages.length - 1
      pages_page = jump_position / pages_per_page + 1
    else
      pages_page = parameters.fetch('pages_page', '1').to_i
      selected_index = nil
    end

    start = (pages_page - 1) * pages_per_page
    slice = pages.slice(start, pages_per_page) || []
    selected = if selected_index
                 page = pages[selected_index]
                 {'position' => selected_index + 1, 'page_number' => page['page_number'],
                  'page_label' => page['page_label'], 'canvas_id' => page['canvas_id'],
                  'previous' => selected_index.positive? ? pages[selected_index - 1] : nil,
                  'next' => selected_index < pages.length - 1 ? pages[selected_index + 1] : nil}
               end
    item = {
      'item_id' => 'demo-long', 'title' => 'Synthetic long item', 'series' => 'Example Weekly',
      'issue_date' => '1927-12-07', 'collection_id' => '/repositories/2/resources/1',
      'aspace_record' => 'https://example.org/records/demo-long',
      'source_manifest_url' => 'https://example.org/manifests/demo-long', 'page_count' => page_count,
      'truncated' => start.positive? || start + slice.length < page_count,
      'pages' => slice, 'pages_page' => pages_page, 'pages_per_page' => pages_per_page,
      'pages_total_pages' => (page_count.to_f / pages_per_page).ceil
    }
    item['selected'] = selected if selected
    item['jump_missed'] = jump_missed if parameters['page_number']
    Response.new('200', JSON.generate(item))
  end

  def synthetic_long_item_api
    search = {
      'query' => 'mascot', 'corpus' => 'example', 'sort' => 'relevance',
      'filters' => {'series' => nil, 'year_from' => nil, 'year_to' => nil,
                    'collection' => nil, 'item' => 'demo-long'},
      'page' => 1, 'per_page' => 25, 'total_items' => 1, 'matching_pages' => 0,
      'partial' => false, 'solr_time_ms' => 1,
      'facets' => {'series' => [], 'decade' => []}, 'results' => []
    }
    FakeAPI.new(search: Response.new('200', JSON.generate(search)),
                item: ->(_endpoint, _id, parameters) { long_item_response(parameters) })
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

    def item(endpoint, id, parameters = {})
      @item_requests << {endpoint: endpoint, id: id, params: parameters}
      return @item_response.call(endpoint, id, parameters) if @item_response.respond_to?(:call)
      @item_response || Response.new('404', '{}')
    end
  end

  class PluginHeadView < ControllerViewContext
    def initialize
      super(ApplicationController.new, [])
    end

    def app_prefix(path)
      "/archives#{path}"
    end

    def stylesheet_link_tag(source, options = {})
      attributes = {href: source}.merge(options)
      "<link #{html_attributes(attributes)}>"
    end

    def javascript_include_tag(source, options = {})
      attributes = {src: source}.merge(options)
      "<script #{html_attributes(attributes)}></script>"
    end

    def render_layout_head
      template = File.expand_path('../public/views/layout_head.html.erb', __dir__)
      ERB.new(File.read(template)).result(binding)
    end

    private

    def html_attributes(attributes)
      attributes.flat_map do |name, value|
        if name == :data
          value.map { |data_name, data_value| html_attribute("data-#{data_name.to_s.tr('_', '-')}", data_value) }
        elsif value == true
          [html_attribute(name, name)]
        elsif value.nil? || value == false
          []
        else
          [html_attribute(name, value)]
        end
      end.join(' ')
    end

    def html_attribute(name, value)
      %(#{name.to_s.tr('_', '-')}="#{CGI.escapeHTML(value.to_s)}")
    end
  end
end
