FROM node:20-alpine AS deps

WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/shared-schemas/package.json packages/shared-schemas/package.json
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile

FROM node:20-alpine AS build

WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/packages/shared-schemas/node_modules ./packages/shared-schemas/node_modules
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/shared-schemas ./packages/shared-schemas
COPY apps/web ./apps/web

ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

RUN pnpm --filter @agentforge/web build

FROM node:20-alpine AS runner

WORKDIR /app
RUN corepack enable
ENV NODE_ENV=production

COPY --from=build /app/package.json /app/pnpm-lock.yaml /app/pnpm-workspace.yaml ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/packages/shared-schemas ./packages/shared-schemas
COPY --from=build /app/apps/web ./apps/web

EXPOSE 3000

CMD ["pnpm", "--filter", "@agentforge/web", "start"]
