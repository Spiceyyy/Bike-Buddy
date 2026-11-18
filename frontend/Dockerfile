# Two targets: "dev" (next dev with a bind mount) and "prod" (a built app).

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM deps AS dev
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

FROM deps AS build
COPY . .
# NEXT_PUBLIC_* values are baked into the browser bundle at build time.
# An empty API URL makes the app call /api/... on its own origin, behind the proxy.
ARG NEXT_PUBLIC_API_URL=""
ARG NEXT_PUBLIC_MAP_CENTER="-73.6,45.52"
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_MAP_CENTER=$NEXT_PUBLIC_MAP_CENTER \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Standalone output: a minimal server with only the modules it imports.
FROM node:22-slim AS prod
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
