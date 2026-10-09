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

  def self.helper_method(*names)
    @declared_helper_methods ||= []
    @declared_helper_methods.concat(names.map(&:to_sym)).uniq!
  end

  def self.declared_helper_methods
    inherited = superclass.respond_to?(:declared_helper_methods) ? superclass.declared_helper_methods : []
    (inherited + (@declared_helper_methods || [])).uniq
  end

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
    render_view_source(File.read(path))
  end

  def render_view_source(source)
    context = ControllerViewContext.new(self, self.class.declared_helper_methods)
    ERB.new(source).result(context.instance_eval { binding })
  end

  def app_prefix(path)
    path
  end
end

class ControllerViewContext
  def initialize(controller, helper_methods)
    @controller = controller
    controller.instance_variables.each do |name|
      instance_variable_set(name, controller.instance_variable_get(name))
    end
    helper_methods.each do |name|
      define_singleton_method(name) do |*args, &block|
        @controller.public_send(name, *args, &block)
      end
    end
  end

  def app_prefix(path)
    @controller.app_prefix(path)
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
