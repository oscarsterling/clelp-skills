# Use Node.js 20 Alpine for minimal footprint
FROM node:20-alpine

# Set working directory
WORKDIR /app

# Copy package files for dependency installation
COPY package*.json ./

# Install all dependencies (including devDependencies for build)
RUN npm ci --ignore-scripts

# Copy source files and build configuration
COPY tsconfig.json ./
COPY src ./src

# Build TypeScript and prune to production only
RUN npm run build && \
    npm prune --production && \
    rm -rf src tsconfig.json

# Set production environment
ENV NODE_ENV=production

# Run as non-root user
USER node

# Entry point from package.json bin field
ENTRYPOINT ["node", "dist/index.js"]
