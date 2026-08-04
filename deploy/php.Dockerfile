FROM php:8.3-fpm-alpine

RUN php -r "foreach (['pdo_sqlite', 'mbstring'] as \$extension) { \
    if (!extension_loaded(\$extension)) { fwrite(STDERR, \"Missing PHP extension: \$extension\\n\"); exit(1); } \
  }"

WORKDIR /app
COPY web /app/web
COPY deploy/php-entrypoint.sh /usr/local/bin/pardubicko-php-entrypoint
RUN chmod 0755 /usr/local/bin/pardubicko-php-entrypoint

ENTRYPOINT ["pardubicko-php-entrypoint"]
CMD ["php-fpm", "-F"]
