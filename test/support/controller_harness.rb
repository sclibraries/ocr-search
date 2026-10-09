require 'cgi'
require 'erb'
require 'logger'

module Rails
  def self.logger
    @logger ||= Logger.new(File::NULL)
  end
end

class ApplicationController
  attr_reader :params, :rendered_status

  def initialize(values = {})
    @params = values.each_with_object({}) { |(key, value), out| out[key.to_sym] = value }
    @rendered_status = 200
  end

  def render(_template, options = {})
    status = options.fetch(:status, 200)
    @rendered_status = {
      'not_found' => 404, :not_found => 404,
      'bad_request' => 400, :bad_request => 400
    }.fetch(status, status)
  end

  def render_view(name)
    path = File.expand_path("../../public/views/ocr_search/#{name}.html.erb", __dir__)
    ERB.new(File.read(path)).result(binding)
  end

  def app_prefix(path)
    path
  end

  def link_to(label, href, attributes = {})
    attrs = attributes.reject { |_key, value| value.nil? }.map do |key, value|
      name = key.to_s.tr('_', '-')
      %( #{name}="#{CGI.escapeHTML(value.to_s)}")
    end.join
    %(<a href="#{CGI.escapeHTML(href.to_s)}"#{attrs}>#{CGI.escapeHTML(label.to_s)}</a>)
  end

  def sanitize(value, tags: [], attributes: [])
    escaped = CGI.escapeHTML(value.to_s)
    tags.include?('mark') ? escaped.gsub('&lt;mark&gt;', '<mark>').gsub('&lt;/mark&gt;', '</mark>') : escaped
  end

  def h(value)
    CGI.escapeHTML(value.to_s)
  end

  def request
    Struct.new(:base_url).new('http://example.test')
  end
end
