require 'json'
require 'uri'
require_relative '../../lib/catalog'
require_relative '../../lib/search_client'

class OcrSearchController < ApplicationController
  ITEM_ID = /\A[a-z0-9-]{1,64}\z/.freeze

  helper_method :search_url, :item_path_url, :item_page_url, :page_url, :page_match,
                :trim_trailing_excerpt_periods

  def index
    @page_title = 'Search digitized newspapers'
    @query = params[:q].to_s.strip
    @catalog = OcrPilot::Catalog::DATA
    @filters = request_filters
    @item_id = @filters['item'].to_s
    @sort = params[:sort].to_s.empty? ? 'relevance' : params[:sort].to_s
    @page_number = params.fetch(:page, '1').to_s
    @per_page = 10
    @facets = {'series' => [], 'decade' => []}
    @results = nil
    raise ArgumentError unless @page_number.match?(/\A[0-9]+\z/) && (1..200).cover?(@page_number.to_i)
    raise ArgumentError unless @item_id.empty? || ITEM_ID.match?(@item_id)
    return if @query.empty?
    raise ArgumentError unless @query.length <= 200

    response = OcrPilot::SearchClient.get(ENV.fetch('OCR_SEARCH_API_URL', ''), search_parameters)
    return unless accept_response(response)

    data = JSON.parse(response.body)
    @filters = normalize_filters(data.fetch('filters', @filters))
    @item_id = @filters['item'].to_s
    @sort = data.fetch('sort', @sort).to_s
    @page_number = data.fetch('page', @page_number).to_s
    @per_page = Integer(data.fetch('per_page', @per_page))
    @total_items = Integer(data.fetch('total_items'))
    @matching_pages = Integer(data.fetch('matching_pages'))
    @partial = data.fetch('partial', false)
    facets = data.fetch('facets', @facets)
    results = data.fetch('results')
    raise IOError unless facets.is_a?(Hash) && results.is_a?(Array)
    @facets = facets
    @results = results
  rescue ArgumentError
    if !@item_id.to_s.empty? && !ITEM_ID.match?(@item_id)
      @filters['item'] = nil
      @item_id = ''
    end
    @error = 'Please choose valid filters and enter a search of up to 200 characters.'
  rescue StandardError => e
    Rails.logger.warn("OCR search unavailable: #{e.class}")
    @error = 'Text search is temporarily unavailable. Please try again shortly.'
  end

  def manifest
    render json: OcrPilot::Catalog.manifest(params[:item].to_s, params[:number].to_s)
  rescue KeyError
    render json: {error: 'Page unavailable'}, status: :not_found
  end

  def show
    @item_id = params[:item].to_s
    @query = params[:q].to_s.strip
    @page_title = 'Search digitized newspapers'
    @filters = request_filters
    @filters['item'] = @item_id
    @sort = params[:sort].to_s.empty? ? 'relevance' : params[:sort].to_s
    @page_number = params.fetch(:page, '1').to_s
    @item_context_query = item_context_query
    @per_page = 25
    @item = nil
    @pages = []
    @matching_pages = []
    @selected_page = nil
    @selected = nil
    @jump_missed = false
    @requested_page_number = params[:page_number].to_s
    requested_page_number = positive_integer(@requested_page_number, maximum: 2_147_483_647)
    @missed_page_number = positive_integer(params[:missed])
    @catalog_page = nil

    raise KeyError unless ITEM_ID.match?(@item_id)
    raise ArgumentError unless @query.length <= 200
    raise ArgumentError unless @requested_page_number.empty? || requested_page_number
    @requested_page_number = requested_page_number.to_s if requested_page_number

    response = OcrPilot::SearchClient.get_item(
      ENV.fetch('OCR_SEARCH_API_URL', ''), @item_id, item_page_parameters
    )
    unless response.code == '200'
      return render(:show, status: :not_found) if response.code == '404'
      @error = error_message(response.code)
      return
    end

    @item = JSON.parse(response.body)
    raise IOError unless @item.fetch('item_id') == @item_id
    @pages = @item.fetch('pages')
    raise IOError unless @pages.is_a?(Array)
    @page_count = Integer(@item.fetch('page_count'))
    @pages_per_page = Integer(@item.fetch('pages_per_page', 50))
    @pages_page = Integer(@item.fetch('pages_page', params.fetch(:pages_page, '1')))
    @pages_total_pages = Integer(@item.fetch('pages_total_pages', (@page_count.to_f / @pages_per_page).ceil))
    @jump_missed = @item.fetch('jump_missed', false) == true
    if requested_page_number
      jump_page = if @jump_missed
                    @pages.last
                  else
                    @pages.find { |page| Integer(page.fetch('page_number')) >= requested_page_number }
                  end
      raise IOError unless jump_page
      return redirect_to(page_url(jump_page, missed: @requested_page_number)) if @jump_missed
      return redirect_to(page_url(jump_page))
    end
    @selected = @item['selected']
    if @selected
      raise IOError unless @selected.is_a?(Hash) && @selected['canvas_id']
      @selected_page = @pages.find { |page| page['canvas_id'] == @selected['canvas_id'] }
      raise IOError unless @selected_page
    end
    raise KeyError if !params[:canvas].to_s.empty? && !@selected_page
    @page_title = @item.fetch('title', 'Search digitized newspapers')
    @catalog_page = catalog_page_for(@item_id, @selected_page) if @selected_page
    @highlight_data = {snippets: []}
    load_item_matches unless @query.empty?
    match = page_match(@selected_page['canvas_id']) if @selected_page
    @highlight_data = {snippets: match ? Array(match['snippets']) : [], partial: @partial} if @selected_page
  rescue KeyError
    @item = nil
    @selected_page = nil
    render(:show, status: :not_found)
  rescue ArgumentError
    @error = 'Please choose a valid item and enter a search of up to 200 characters.'
    render(:show, status: :bad_request)
  rescue StandardError => e
    Rails.logger.warn("OCR item unavailable: #{e.class}")
    @error = 'Text search is temporarily unavailable. Please try again shortly.'
  end

  def search_url(changes = {})
    state = {
      'q' => @query,
      'series' => @filters['series'],
      'year_from' => @filters['year_from'],
      'year_to' => @filters['year_to'],
      'collection' => @filters['collection'],
      'item' => @filters['item'],
      'sort' => @sort,
      'per_page' => (@per_page.to_i == 10 ? nil : @per_page)
    }.merge(changes)
    state.delete_if { |_key, value| value.nil? || value.to_s.empty? }
    query_string = URI.encode_www_form(state)
    path = helpers.app_prefix('/digital-text')
    query_string.empty? ? path : path + '?' + query_string
  end

  def item_path_url(item_id)
    raise ArgumentError, 'Invalid item identifier' unless ITEM_ID.match?(item_id.to_s)
    helpers.app_prefix('/digital-text/items/' + item_id.to_s)
  end

  def item_page_url(item_id, canvas_id = nil, changes = {})
    query = item_context_query
    query['canvas'] = canvas_id unless canvas_id.to_s.empty?
    query.merge!(changes)
    path = item_path_url(item_id)
    query.delete_if { |_key, value| value.nil? || value.to_s.empty? }
    query.empty? ? path : path + '?' + URI.encode_www_form(query)
  end

  def page_match(canvas_id)
    @matching_pages.find { |page| page['canvas_id'] == canvas_id }
  end

  def trim_trailing_excerpt_periods(html)
    excerpt = html.to_s.dup
    loop do
      break unless excerpt.sub!(/\.+(?=(?:<\/mark>)*\z)/, '')
    end
    excerpt
  end

  def page_url(page, pages_page: @pages_page, missed: nil)
    changes = {'pages_page' => pages_page}
    changes['missed'] = missed unless missed.nil?
    item_page_url(@item_id, page['canvas_id'], changes)
  end

  private

  def request_filters
    {
      'series' => nonblank(params[:series]),
      'year_from' => nonblank(params[:year_from]),
      'year_to' => nonblank(params[:year_to]),
      'collection' => nonblank(params[:collection]),
      'item' => nonblank(params[:item])
    }
  end

  def normalize_filters(filters)
    %w[series year_from year_to collection item].each_with_object({}) do |key, normalized|
      value = filters[key] || filters[key.to_sym]
      normalized[key] = value.nil? || value.to_s.empty? ? nil : value.to_s
    end
  end

  def search_parameters
    parameters = {'q' => @query, 'sort' => @sort, 'page' => @page_number}
    @filters.each { |key, value| parameters[key] = value unless value.nil? }
    per_page = params[:per_page].to_s
    parameters['per_page'] = per_page unless per_page.empty?
    parameters
  end

  def item_page_parameters
    parameters = {'pages_per_page' => '50'}
    if !params[:canvas].to_s.empty?
      parameters['canvas'] = params[:canvas].to_s
    elsif !params[:page_number].to_s.empty?
      parameters['page_number'] = params[:page_number].to_s
    else
      parameters['pages_page'] = params[:pages_page].to_s.empty? ? '1' : params[:pages_page].to_s
    end
    parameters
  end

  def item_context_query
    query = {'q' => @query}
    %w[series year_from year_to collection].each do |key|
      query[key] = @filters[key] if @filters && @filters[key]
    end
    query['sort'] = @sort if @sort && @sort != 'relevance'
    query['page'] = @page_number if @filters && @filters['item'] && @page_number && @page_number != '1'
    query['per_page'] = '25' if @filters && @filters['item']
    query.delete_if { |_key, value| value.nil? || value.to_s.empty? }
    query
  end

  def accept_response(response)
    return true if response.code == '200'
    @error = error_message(response.code)
    false
  end

  def error_message(code)
    case code.to_s
    when '400'
      'Please use words or a phrase in quotation marks, up to 200 characters.'
    when '429'
      'Search is busy; try again in a moment.'
    when '503'
      'Text search is temporarily unavailable. Please try again shortly.'
    else
      'Text search is temporarily unavailable. Please try again shortly.'
    end
  end

  def nonblank(value)
    value.to_s.empty? ? nil : value.to_s
  end

  def positive_integer(value, maximum: nil)
    string = value.to_s
    return nil unless string.match?(/\A[0-9]+\z/)

    number = string.to_i
    return nil unless number.positive?
    return nil if maximum && number > maximum

    number
  end

  def catalog_page_for(item_id, selected_page)
    return nil unless OcrPilot::Catalog::DATA.key?(item_id)
    OcrPilot::Catalog.page(item_id, selected_page['canvas_id'])
  rescue KeyError
    nil
  end

  def load_item_matches
    response = OcrPilot::SearchClient.get(ENV.fetch('OCR_SEARCH_API_URL', ''), {
      'q' => @query,
      'item' => @item_id,
      'sort' => params[:sort].to_s.empty? ? 'relevance' : params[:sort].to_s,
      'page' => params.fetch(:page, '1').to_s,
      'per_page' => '25',
      'series' => nonblank(params[:series]),
      'year_from' => nonblank(params[:year_from]),
      'year_to' => nonblank(params[:year_to]),
      'collection' => nonblank(params[:collection])
    })
    unless response.code == '200'
      @error = error_message(response.code)
      return
    end

    data = JSON.parse(response.body)
    @partial = data.fetch('partial', false)
    result = Array(data.fetch('results')).find { |row| row['item_id'] == @item_id }
    @matching_pages = result ? Array(result.fetch('pages')) : []
  end
end
