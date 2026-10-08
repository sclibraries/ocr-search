require 'json'
module OcrPilot
  module Catalog
    DATA = JSON.parse(File.read(File.join(__dir__, 'catalog.json'))).freeze
    def self.item(id)
      DATA.fetch(id)
    end
    # A selected-canvas manifest uses the viewer's existing manifest contract.
    # Canvas IDs are opaque identifiers; no request is made to their origin.
    def self.manifest(id, number)
      selected = item(id).fetch('pages').find { |page| page['number'].to_s == number }
      raise KeyError, 'Page reference unavailable' unless selected
      service = selected.fetch('service')
      {
        '@context' => 'http://iiif.io/api/presentation/2/context.json',
        '@id' => "urn:ocr-pilot:#{id}:#{number}", '@type' => 'sc:Manifest',
        'label' => selected.fetch('label'),
        'sequences' => [{'@type' => 'sc:Sequence', 'canvases' => [{
          '@id' => selected.fetch('canvas'), '@type' => 'sc:Canvas',
          'label' => selected.fetch('label'),
          'images' => [{'@type' => 'oa:Annotation', 'motivation' => 'sc:painting',
            'on' => selected.fetch('canvas'), 'resource' => {
              '@id' => service + '/full/1200,/0/default.jpg', '@type' => 'dctypes:Image',
              'service' => {'@id' => service, 'profile' => 'http://iiif.io/api/image/2/level2.json'}
            }}]
        }]}]
      }
    end
    def self.page(id, canvas)
      item(id).fetch('pages').find { |page| page['canvas'] == canvas } || raise(KeyError, 'Page reference unavailable')
    end
  end
end
