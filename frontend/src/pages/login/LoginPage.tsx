import { useEffect, useState } from "react";
import { api } from "@/api/client";

export function LoginPage() {
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ authorize_url: string }>("/api/auth/feishu/login")
      .then((r) => setAuthorizeUrl(r.authorize_url))
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="max-w-sm w-full space-y-4 p-8 rounded-lg shadow bg-white dark:bg-gray-900">
        <h1 className="text-xl font-semibold">登录 ticket-hub</h1>
        <p className="text-sm text-gray-500">飞书扫码登录是唯一入口（决策 D19）。</p>
        {error && <p className="text-sm text-red-600">{error}</p>}
        {authorizeUrl ? (
          <a
            href={authorizeUrl}
            className="block w-full text-center bg-blue-600 hover:bg-blue-700 text-white py-2 rounded"
          >
            飞书扫码登录
          </a>
        ) : (
          <button disabled className="w-full bg-gray-300 text-gray-600 py-2 rounded">
            正在加载…
          </button>
        )}
      </div>
    </div>
  );
}
