FROM alpine:3.19
RUN apk add --no-cache bash curl docker-cli
COPY watch-logs.sh /watch-logs.sh
RUN chmod +x /watch-logs.sh
CMD ["/watch-logs.sh"]

