import type { NextRequest } from "next/server";

import { proxyRoute } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: { path: string[] } };

export async function GET(req: NextRequest, context: RouteContext) {
  return proxyRoute(req, context);
}

export async function POST(req: NextRequest, context: RouteContext) {
  return proxyRoute(req, context);
}

export async function PUT(req: NextRequest, context: RouteContext) {
  return proxyRoute(req, context);
}

export async function PATCH(req: NextRequest, context: RouteContext) {
  return proxyRoute(req, context);
}

export async function DELETE(req: NextRequest, context: RouteContext) {
  return proxyRoute(req, context);
}

export async function HEAD(req: NextRequest, context: RouteContext) {
  return proxyRoute(req, context);
}

export async function OPTIONS(req: NextRequest, context: RouteContext) {
  return proxyRoute(req, context);
}
