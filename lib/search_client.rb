require 'net/http'
require 'uri'

module OcrPilot
  module SearchClient
    SEARCH_PARAMETERS = %w[q series year_from year_to collection item sort page per_page].freeze
    ITEM_ID = /\A[a-z0-9-]{1,64}\z/.freeze

    def self.uri(endpoint, parameters = {})
      uri = search_endpoint(endpoint)
      query = parameters.each_with_object({}) do |(key, value), result|
        name = key.to_s
        next unless SEARCH_PARAMETERS.include?(name)
        next if value.nil? || value.to_s.empty?
        result[name] = value.to_s
      end
      uri.query = URI.encode_www_form(query)
      uri
    end

    def self.item_uri(endpoint, id)
      raise ArgumentError, 'Invalid item identifier' unless ITEM_ID.match?(id.to_s)
      uri = search_endpoint(endpoint)
      raise IOError, 'Search endpoint must end in /search' unless uri.path.match?(%r{/search\z})
      uri.path = uri.path.sub(/search\z/, "items/#{id}")
      uri.query = nil
      uri
    end

    def self.get(endpoint, parameters = {})
      request(uri(endpoint, parameters))
    end

    def self.get_item(endpoint, id)
      request(item_uri(endpoint, id))
    end

    def self.search_endpoint(endpoint)
      uri = URI(endpoint)
      raise IOError, 'Search endpoint is not configured' unless %w[http https].include?(uri.scheme) && uri.host
      uri
    end
    private_class_method :search_endpoint

    def self.request(uri)
      Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https', open_timeout: 3, read_timeout: 8) do |http|
        http.get(uri.request_uri)
      end
    end
    private_class_method :request
  end
end
