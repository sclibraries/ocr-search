require 'json'
require_relative '../../lib/catalog'
require_relative '../../lib/search_client'

class OcrSearchController < ApplicationController
  def index
    @page_title = 'Search digitized newspapers'
    @query = params[:q].to_s.strip
    @item_id = params[:item].to_s
    @catalog = OcrPilot::Catalog::DATA
    @page_number = params.fetch(:page, '1').to_s
    return if @query.empty?
    raise ArgumentError unless @page_number.match?(/\A[0-9]+\z/) && (1..200).cover?(@page_number.to_i)
    raise ArgumentError unless @query.length <= 200 && (@item_id.empty? || @catalog.key?(@item_id))
    response = OcrPilot::SearchClient.get(ENV.fetch('OCR_SEARCH_API_URL', ''), @query, @item_id, @page_number)
    if response.code == '400'
      @error = 'Please use words or a phrase in quotation marks, up to 200 characters.'
      return
    end
    raise IOError unless response.code == '200'
    data = JSON.parse(response.body)
    @total = Integer(data.fetch('total_pages'))
    @partial = data.fetch('partial', false)
    @results = data.fetch('results')
    raise IOError unless @results.is_a?(Array)
  rescue ArgumentError
    @error = 'Please choose an issue and enter a search of up to 200 characters.'
  rescue StandardError => e
    Rails.logger.warn("OCR pilot search unavailable: #{e.class}")
    @error = 'Text search is temporarily unavailable. Please try again shortly.'
  end

  def manifest
    render json: OcrPilot::Catalog.manifest(params[:item].to_s, params[:number].to_s)
  rescue KeyError
    render json: {error: 'Page unavailable'}, status: :not_found
  end

  def show
    @item_id = params[:item].to_s
    @item = OcrPilot::Catalog.item(@item_id)
    @page = OcrPilot::Catalog.page(@item_id, params[:canvas].to_s)
    @query = params[:q].to_s.first(200)
    @page_title = "#{@item['title']} — Page #{@page['number']}"
    @highlight_data = {snippets: []}
    unless @query.strip.empty?
      begin
        response = OcrPilot::SearchClient.get(ENV.fetch('OCR_SEARCH_API_URL', ''), @query, @item_id)
        raise IOError unless response.code == '200'
        data = JSON.parse(response.body)
        result = data.fetch('results').find { |row| row['item_id'] == @item_id && row['canvas_id'] == @page['canvas'] }
        @highlight_data = {snippets: result ? result.fetch('snippets', []) : [], partial: data['partial']}
      rescue StandardError => e
        Rails.logger.warn("OCR page highlights unavailable: #{e.class}")
        @highlight_data = {snippets: [], error: 'Search highlights are temporarily unavailable. The page image is still available.'}
      end
    end
  rescue KeyError
    @page = nil
    @page_title = 'Page unavailable'
    render :show, status: :not_found
  end
end
