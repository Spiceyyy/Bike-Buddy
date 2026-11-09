FROM eclipse-temurin:21-jre

ARG GRAPHHOPPER_VERSION=11.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/graphhopper
ADD https://repo1.maven.org/maven2/com/graphhopper/graphhopper-web/${GRAPHHOPPER_VERSION}/graphhopper-web-${GRAPHHOPPER_VERSION}.jar graphhopper.jar
COPY config.yml entrypoint.sh ./
COPY custom_models ./custom_models

EXPOSE 8989
CMD ["sh", "entrypoint.sh"]
