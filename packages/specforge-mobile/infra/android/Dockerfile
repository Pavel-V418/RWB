# Образ песочницы Android-этапа: specforge/android:jdk21-api36
#
# Сборка (контекст — корень пакета packages/specforge-mobile, каркас — его android/):
#   docker build -f packages/specforge-mobile/infra/android/Dockerfile -t specforge/android:jdk21-api36 \
#     packages/specforge-mobile
#
# Что внутри: JDK 21, Android SDK (platform 36, build-tools 35/36), git, python3,
# прогретые кэши — Gradle-дистрибутив, зависимости каркаса и образ Android для Robolectric.
# Кэши лежат вне /work (рабочая папка агента монтируется томом и их не перекрывает).
# Без интернета шлюз запускать с `./gradlew --offline ...`.

FROM eclipse-temurin:21-jdk-jammy

ARG CMDLINE_TOOLS=11076708
ARG TEMPLATE_DIR=android

ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    GRADLE_USER_HOME=/opt/gradle-home \
    HOME=/opt/home \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8
ENV PATH=$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends git unzip curl ca-certificates python3 fontconfig fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# Android SDK: только то, что нужно каркасу (compileSdk 36; AGP 8.11 по умолчанию берёт build-tools 35)
RUN mkdir -p $ANDROID_HOME/cmdline-tools \
 && curl -fsSL -o /tmp/cmdline.zip \
      https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS}_latest.zip \
 && unzip -q /tmp/cmdline.zip -d $ANDROID_HOME/cmdline-tools \
 && mv $ANDROID_HOME/cmdline-tools/cmdline-tools $ANDROID_HOME/cmdline-tools/latest \
 && rm /tmp/cmdline.zip \
 && yes | sdkmanager --licenses > /dev/null \
 && sdkmanager --install "platforms;android-36" "build-tools;35.0.0" "build-tools;36.0.0" > /dev/null

# Java берёт user.home из /etc/passwd (/root), а не из $HOME: без этого Robolectric кладёт android-all
# в /root/.m2, недоступный песочнице. JAVA_TOOL_OPTIONS наследуют и Gradle, и JVM тестов.
ENV JAVA_TOOL_OPTIONS=-Duser.home=/opt/home
RUN mkdir -p /opt/home

# Прогрев: та же сборка, что в шлюзе, на самом каркасе — версии зависимостей совпадут с агентом.
# Скачиваются Gradle 8.14.3, плагины, библиотеки и android-all для Robolectric (в $HOME/.m2).
COPY ${TEMPLATE_DIR}/ /tmp/warm/
COPY specforge_mobile/tools/android_gate/screens.py /opt/specforge/screens.py
# Один шаг (иначе chmod копирует весь кэш во второй слой и образ раздувается вдвое).
# Ошибка прогрева валит сборку образа (иначе получим образ без кэша и долгие прогоны).
# После прогрева удаляем рабочее состояние Gradle (daemon/native/.tmp): это не кэш, и Gradle делает
# на нём chmod 700 — от не-root пользователя песочницы это возможно только для своих, свежих папок.
RUN set -e \
 && cd /tmp/warm \
 && rm -rf build app/build .gradle local.properties screenshots \
 && chmod +x gradlew \
 && ./gradlew --no-daemon assembleDebug testDebugUnitTest recordRoborazziDebug \
 && ls screenshots/*.png \
 && rm -rf /tmp/warm $GRADLE_USER_HOME/daemon $GRADLE_USER_HOME/native $GRADLE_USER_HOME/.tmp \
 && chmod -R a+rwX $GRADLE_USER_HOME $HOME $ANDROID_HOME

# git: платформа делает снимки и откаты рабочей папки
RUN git config --system --add safe.directory '*' \
 && git config --system user.email "sandbox@specforge.local" \
 && git config --system user.name "specforge-sandbox"

WORKDIR /work
