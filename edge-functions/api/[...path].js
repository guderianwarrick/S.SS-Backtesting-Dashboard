/**
 * EdgeOne Edge Function — API Proxy
 * 
 * 将所有 /api/* 请求代理到后端 FastAPI 服务器
 * 静态资源由 EdgeOne 直接响应
 * 
 * 使用前：在阿里云安全组放行服务器 8080 端口（TCP）
 */

const BACKEND_HOST = '123.57.65.245';
const BACKEND_PORT = 8080;

export default function onRequest(context) {
  const { request } = context;
  const url = new URL(request.url);
  const path = url.pathname;
  
  // 只代理 API 请求
  if (!path.startsWith('/api/')) {
    // 非 API 请求：返回静态资源
    return context.env.ASSETS.fetch(request);
  }
  
  // 构造后端 URL
  const backendUrl = `http://${BACKEND_HOST}:${BACKEND_PORT}${path}${url.search}`;
  
  // 转发请求
  return fetch(backendUrl, {
    method: request.method,
    headers: request.headers,
    body: ['GET', 'HEAD'].includes(request.method) ? null : request.body,
  });
}