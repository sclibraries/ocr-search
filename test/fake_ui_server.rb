#!/usr/bin/env ruby
require 'json'
require 'webrick'
require_relative 'support/controller_harness'
require_relative '../public/controllers/ocr_search_controller'

module OcrSearchFakeAPI
  Response = Struct.new(:code, :body)

  def self.search(parameters)
    items = sample_items
    items.select! { |item| item['item_id'] == parameters['item'] } if parameters['item']
    items.select! { |item| item['series'] == parameters['series'] } if parameters['series']
    items.select! { |item| item['collection_id'] == parameters['collection'] } if parameters['collection']
    if parameters['year_from'] || parameters['year_to']
      first = parameters['year_from'].to_i
      last = parameters['year_to'].to_i
      items.select! do |item|
        year = item['issue_date'].to_i
        (first.zero? || year >= first) && (last.zero? || year <= last)
      end
    end

    page = (parameters['page'] || '1').to_i
    per_page = (parameters['per_page'] || '10').to_i
    matching_pages = items.sum { |item| item['matching_pages'] }
    if parameters['item']
      matching = items.first
      groups = matching ? [matching.reject { |key, _value| key == 'all_pages' }
                          .merge('pages' => matching['all_pages'].first(per_page))] : []
    else
      groups = items.map do |item|
        item.reject { |key, _value| key == 'all_pages' }.merge('pages' => item['all_pages'].first(3))
      end
      groups = groups.sort_by { |item| [item['issue_date'].to_s, item['item_id']] } if parameters['sort'] == 'date_asc'
      groups.reverse! if parameters['sort'] == 'date_desc'
      groups = groups.slice((page - 1) * per_page, per_page) || []
    end

    payload = {
      'query' => parameters['q'], 'corpus' => 'example',
      'sort' => parameters['sort'] || 'relevance',
      'filters' => {
        'series' => parameters['series'], 'year_from' => integer_or_nil(parameters['year_from']),
        'year_to' => integer_or_nil(parameters['year_to']), 'collection' => parameters['collection'],
        'item' => parameters['item']
      },
      'page' => page, 'per_page' => per_page, 'total_items' => parameters['item'] ? (groups.empty? ? 0 : 1) : items.length,
      'matching_pages' => matching_pages, 'partial' => false, 'solr_time_ms' => 1,
      'facets' => {
        'series' => [
          {'value' => 'Example Weekly', 'count' => 2},
          {'value' => 'Other Weekly', 'count' => 1}
        ],
        'decade' => [
          {'value' => 1920, 'count' => 2},
          {'value' => 1930, 'count' => 1}
        ]
      },
      'results' => groups
    }
    Response.new('200', JSON.generate(payload))
  end

  def self.item(id, parameters = {})
    item = sample_items.find { |row| row['item_id'] == id }
    return Response.new('404', '{}') unless item

    pages = item['all_pages'].map do |page|
      page.reject { |key, _value| key == 'snippets' }
    end
    page_count = pages.length
    pages_per_page = (parameters['pages_per_page'] || '50').to_i
    selected_index = nil
    jump_missed = false
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
      pages_page = (parameters['pages_page'] || '1').to_i
    end
    start = (pages_page - 1) * pages_per_page
    slice = pages.slice(start, pages_per_page) || []
    details = item.reject { |key, _value| %w[all_pages matching_pages].include?(key) }
    details['page_count'] = page_count
    details['pages_page'] = pages_page
    details['pages_per_page'] = pages_per_page
    details['pages_total_pages'] = (page_count.to_f / pages_per_page).ceil
    details['truncated'] = start.positive? || start + slice.length < page_count
    details['pages'] = slice
    details['jump_missed'] = jump_missed if parameters['page_number']
    if selected_index
      selected = pages[selected_index]
      details['selected'] = {
        'position' => selected_index + 1, 'page_number' => selected['page_number'],
        'page_label' => selected['page_label'], 'canvas_id' => selected['canvas_id'],
        'previous' => selected_index.positive? ? selected_page(pages[selected_index - 1]) : nil,
        'next' => selected_index < page_count - 1 ? selected_page(pages[selected_index + 1]) : nil
      }
    end
    Response.new('200', JSON.generate(details))
  end

  def self.selected_page(page)
    %w[page_number page_label canvas_id].to_h { |key| [key, page[key]] }
  end

  def self.sample_items
    [
      sample_item('demo-a', 'Example Weekly, 1927-12-07', 'Example Weekly', '1927-12-07', 2, 8),
      sample_item('demo-b', 'Other Weekly, 1931-04', 'Other Weekly', '1931-04', 1, 2)
    ]
  end

  def self.sample_item(id, title, series, date, count, first_page)
    pages = (0...count).map do |offset|
      number = first_page + offset
      {
        'id' => "example:#{id}:#{number}", 'page_number' => number,
        'page_label' => "Page #{number}", 'canvas_id' => "https://example.org/canvas/#{id}/#{number}",
        'snippets' => [{'html' => "A <mark>mascot</mark> appears on page #{number}.",
                        'pages' => [], 'regions' => [], 'highlights' => []}]
      }
    end
    {
      'item_id' => id, 'title' => title, 'series' => series, 'issue_date' => date,
      'collection_id' => '/repositories/2/resources/1',
      'aspace_record' => "https://example.org/records/#{id}",
      'source_manifest_url' => "https://example.org/manifests/#{id}",
      'matching_pages' => count, 'all_pages' => pages
    }
  end

  def self.integer_or_nil(value)
    value.to_s.empty? ? nil : value.to_i
  end
  private_class_method :integer_or_nil
end

module OcrPilot
  module SearchClient
    def self.get(_endpoint, parameters = {})
      OcrSearchFakeAPI.search(parameters)
    end

    def self.get_item(_endpoint, id, parameters = {})
      OcrSearchFakeAPI.item(id, parameters)
    end
  end
end

ENV['OCR_SEARCH_API_URL'] = 'http://fake-api.test/api/ocr/search'
server = WEBrick::HTTPServer.new(
  Port: Integer(ENV.fetch('OCR_TEST_PORT', '49159')), BindAddress: '127.0.0.1',
  Logger: WEBrick::Log.new(File::NULL), AccessLog: []
)

server.mount_proc('/assets/ocr_search.css') do |_request, response|
  response['Content-Type'] = 'text/css; charset=utf-8'
  response.body = File.read(File.expand_path('../public/assets/ocr_search.css', __dir__))
end

server.mount_proc('/') do |request, response|
  response['Content-Type'] = 'text/html; charset=utf-8'
  if request.path == '/digital-text'
    controller = OcrSearchController.new(request.query)
    controller.index
    body = controller.render_view('index')
  elsif request.path =~ %r{\A/digital-text/items/([a-z0-9-]{1,64})\z}
    controller = OcrSearchController.new(request.query.merge('item' => Regexp.last_match(1)))
    controller.show
    response.status = controller.rendered_status
    body = controller.render_view('show')
  else
    response.status = 404
    body = '<h1>Not found</h1>'
  end
  response.body = <<~HTML
    <!doctype html>
    <html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>OCR Search test fixture</title><link rel="stylesheet" href="/assets/ocr_search.css"></head>
    <body>#{body}</body></html>
  HTML
end

trap('INT') { server.shutdown }
trap('TERM') { server.shutdown }
puts "FAKE_UI_URL=http://127.0.0.1:#{server.config[:Port]}"
$stdout.flush
server.start
