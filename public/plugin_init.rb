Rails.application.config.after_initialize do
  I18n.backend.store_translations(:en, ocr_search: {navigation: 'Newspaper text'})
  $MAIN_MENU.push(['/digital-text', 'ocr_search.navigation'])

  Rails.application.routes.prepend do
    scope AppConfig[:public_proxy_prefix] do
      get '/digital-text/manifests/:item/:number', to: 'ocr_search#manifest', defaults: {format: 'json'}
      get '/digital-text', to: 'ocr_search#index'
      get '/digital-text/items/:item', to: 'ocr_search#show'
    end
  end
end
