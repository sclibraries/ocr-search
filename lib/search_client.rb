require 'net/http'
module OcrPilot
  module SearchClient
    def self.uri(endpoint, query, item, page = '1')
      uri = URI(endpoint)
      raise IOError, 'Search endpoint is not configured' unless %w[http https].include?(uri.scheme) && uri.host
      parameters = {q: query, per_page: 25, page: page}
      parameters[:item] = item unless item.empty?
      uri.query = URI.encode_www_form(parameters)
      uri
    end

    def self.get(endpoint, query, item, page = '1')
      uri = self.uri(endpoint, query, item, page)
      Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https', open_timeout: 3, read_timeout: 8) do |http|
        http.get(uri.request_uri)
      end
    end
  end
end
