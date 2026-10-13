require 'digest'

module OcrSearchAssetVersions
  ASSET_NAMES = %w[ocr_search.css ocr_highlights.css ocr_highlights.js].freeze

  def self.calculate(asset_directory)
    ASSET_NAMES.each_with_object({}) do |name, versions|
      versions[name] = Digest::SHA256.file(File.join(asset_directory, name)).hexdigest[0, 12]
    end.freeze
  end

  VERSIONS = calculate(File.expand_path('assets', __dir__))
end
