# specforge/android:21 — песочница Android: JDK 21 (Gradle 8.14.3 не работает на JDK 25),
# Android SDK (cmdline-tools, platform, build-tools), git. Kotlin 2.2.21 и Gradle приходят
# из проекта (wrapper), их кэш — /cache/gradle.
# Версии SDK сверить с участником, который пишет Android-агента (compileSdk / build-tools).
# Прогрев Gradle: положить каркас проекта в infra/images/android/skeleton/ — при сборке
# выполнится ./gradlew assembleDebug, зависимости останутся в /cache/gradle.
# Сборка: infra/images/build.sh android
ARG JDK_TAG=21-jdk-jammy
FROM eclipse-temurin:${JDK_TAG}

ARG CMDLINE_TOOLS=11076708
ARG COMPILE_SDK=35
ARG BUILD_TOOLS=35.0.0
ARG PREWARM=true
ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    GRADLE_USER_HOME=/cache/gradle \
    GRADLE_OPTS="-Dorg.gradle.daemon=false -Dorg.gradle.workers.max=2"
ENV PATH="${PATH}:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools"

RUN apt-get update \
    && apt-get install -y --no-install-recommends git coreutils unzip curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system user.email "sandbox@specforge.local" \
    && git config --system user.name "specforge" \
    && mkdir -p /cache/nuget /cache/npm /cache/gradle /cache/pip /cache/trivy /work \
    && chmod -R 1777 /cache

RUN set -e; mkdir -p "${ANDROID_HOME}/cmdline-tools"; \
    curl -fsSL -o /tmp/tools.zip \
      "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS}_latest.zip"; \
    unzip -q /tmp/tools.zip -d "${ANDROID_HOME}/cmdline-tools"; \
    mv "${ANDROID_HOME}/cmdline-tools/cmdline-tools" "${ANDROID_HOME}/cmdline-tools/latest"; \
    rm /tmp/tools.zip; \
    yes | sdkmanager --licenses >/dev/null; \
    sdkmanager "platform-tools" "platforms;android-${COMPILE_SDK}" "build-tools;${BUILD_TOOLS}" >/dev/null; \
    chmod -R a+rwX "${ANDROID_HOME}"

COPY skeleton/ /tmp/skeleton/
RUN if [ "$PREWARM" = "true" ] && [ -x /tmp/skeleton/gradlew ]; then \
      cd /tmp/skeleton && ./gradlew --no-daemon assembleDebug testDebugUnitTest; \
    fi \
    && rm -rf /tmp/skeleton \
    && chmod -R 1777 /cache

WORKDIR /work
