FROM node:20-alpine

WORKDIR /app

COPY package.json ./
RUN npm install --legacy-peer-deps

COPY . .

ENV NEXT_TELEMETRY_DISABLED=1
ENV BACKEND_URL=https://visionretail-iq.onrender.com
ENV NEXT_PUBLIC_BACKEND_URL=https://visionretail-iq.onrender.com

RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]