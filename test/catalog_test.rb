require 'minitest/autorun'
require_relative '../lib/catalog'
class CatalogTest < Minitest::Test
  def test_exact_canvas_and_digital_image
    page = OcrPilot::Catalog.page('scw', 'https://compass.fivecolleges.edu/node/1344523/canvas/6534396')
    assert_equal 8, page.fetch('number')
    assert_equal 'https://digital.smith.edu/iiif/2/2024-10%2Fsmith_ca_scw_19271207_0008.tif', page.fetch('service')
  end
  def test_stale_and_cross_item_canvas_are_rejected
    assert_raises(KeyError) { OcrPilot::Catalog.page('scw', 'missing') }
    assert_raises(KeyError) { OcrPilot::Catalog.page('soph', 'https://compass.fivecolleges.edu/node/1344523/canvas/6534396') }
    assert_raises(KeyError) { OcrPilot::Catalog.item('../secret') }
  end
  def test_all_pages_available
    assert_equal 14, OcrPilot::Catalog.item('scw').fetch('pages').length
    assert_equal 12, OcrPilot::Catalog.item('soph').fetch('pages').length
  end
end
class ManifestTest < Minitest::Test
  def test_manifest_selects_exact_page
    manifest = OcrPilot::Catalog.manifest('scw', '8')
    canvases = manifest.fetch('sequences')[0].fetch('canvases')
    assert_equal 1, canvases.length
    assert_equal 'https://compass.fivecolleges.edu/node/1344523/canvas/6534396', canvases[0]['@id']
    assert_match %r{\Ahttps://digital.smith.edu/}, canvases[0]['images'][0]['resource']['service']['@id']
    assert_raises(KeyError) { OcrPilot::Catalog.manifest('scw', '0') }
    assert_raises(KeyError) { OcrPilot::Catalog.manifest('scw', '8junk') }
  end
end
