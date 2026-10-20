FROM node:22-bookworm-slim

WORKDIR /app

# Install curl for container healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy full application code
COPY . .

# Build argument for API URL (defaults to http://localhost:8055 for browser access)
ARG NEXT_PUBLIC_API_URL=http://localhost:8055
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NODE_ENV=production \
    PORT=3055

# Build production bundle
RUN npm run build

EXPOSE 3055

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:3055/ || exit 1

CMD ["npm", "run", "start"]
