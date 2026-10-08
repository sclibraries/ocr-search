FROM solr:9.8.1@sha256:86a7f3fbec6596b4ee1122b6a862f41eb4b7f41a164abbc169ba9ee03e41525b
USER root
RUN mkdir -p /opt/solr/contrib/ocr/lib \
    && curl -fL --retry 3 https://github.com/dbmdz/solr-ocrhighlighting/releases/download/0.10.1/solr-ocrhighlighting-0.10.1.jar -o /opt/solr/contrib/ocr/lib/ocr.jar \
    && echo 'd10b86684004ebeec3d938a64608135e80c1cef58a176dacb9a556f0b5a78823  /opt/solr/contrib/ocr/lib/ocr.jar' | sha256sum -c -
COPY config /opt/ocr-config
USER solr
