// 产品内提单 · 租户接入管理（ADR-0017 D4，admin-only）。
// 密钥只在创建 / 轮换的响应里出现一次，页面用一次性弹层展示，不落任何存储。
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, patchByPath, postByPath } from "@/api/client";
import type { paths } from "@/api/types";
import { AdminTabs } from "../AdminTabs";
import { hubErrMsg } from "@/components/hubActions";

type TenantOut = paths["/api/admin/tenants"]["get"]["responses"]["200"]["content"]["application/json"][number];
type TenantCreated = paths["/api/admin/tenants"]["post"]["responses"]["201"]["content"]["application/json"];

const INPUT = "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px]";
const BTN = "px-3 py-1.5 rounded-[7px] bg-hub-teal text-white text-[12px] font-semibold disabled:opacity-50";
const BTN_GHOST = "px-3 py-1.5 rounded-[7px] border border-hub-border bg-white text-[12px] text-hub-textSecondary hover:border-hub-teal";

function SecretModal({ data, onClose }: { data: TenantCreated; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const portalOrigin = window.location.origin + (import.meta.env.VITE_PUBLIC_BASE ?? "/");
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6" onClick={onClose}>
      <div className="bg-white rounded-[12px] p-5 w-full max-w-[560px] shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="text-[14px] font-bold text-hub-textPrimary mb-1">租户「{data.name}」接入密钥</div>
        <p className="text-[12px] text-hub-rose mb-3">
          该密钥仅显示这一次，请立即交给接入方妥善保存；丢失只能「轮换密钥」重新生成。
        </p>
        <div className="font-mono text-[12px] break-all bg-hub-panel border border-hub-border rounded-[8px] p-3 select-all">{data.hmac_secret}</div>
        <div className="mt-3 text-[11.5px] text-hub-textMuted leading-relaxed">
          接入方式：服务端计算 <code>sign = HMAC_SHA256(secret, "{data.code}.{"{external_uid}"}.{"{ts}"}")</code>，
          POST <code>/api/portal/auth/token</code> 换门户 token 后，以{" "}
          <code>{portalOrigin}portal.html?token=&lt;token&gt;#/</code> 打开 H5。
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            className={BTN_GHOST}
            onClick={() => {
              void navigator.clipboard?.writeText(data.hmac_secret);
              setCopied(true);
            }}
          >
            {copied ? "已复制" : "复制密钥"}
          </button>
          <button className={BTN} onClick={onClose}>
            我已保存
          </button>
        </div>
      </div>
    </div>
  );
}

export function TenantsPage() {
  const qc = useQueryClient();
  const tenants = useQuery({ queryKey: ["admin", "tenants"], queryFn: () => api.get("/api/admin/tenants") as Promise<TenantOut[]> });
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [secret, setSecret] = useState<TenantCreated | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["admin", "tenants"] });

  const create = useMutation({
    mutationFn: () => api.post("/api/admin/tenants", { code: code.trim(), name: name.trim() }) as Promise<TenantCreated>,
    onSuccess: (d) => {
      setSecret(d);
      setCode("");
      setName("");
      setErr(null);
      refresh();
    },
    onError: (e) => setErr(hubErrMsg(e)),
  });
  const rotate = useMutation({
    mutationFn: (id: number) =>
      postByPath("/api/admin/tenants/{tenant_id}/rotate-secret", { tenant_id: id }, {}) as Promise<TenantCreated>,
    onSuccess: (d) => setSecret(d),
    onError: (e) => setErr(hubErrMsg(e)),
  });
  const toggle = useMutation({
    mutationFn: (t: TenantOut) =>
      patchByPath("/api/admin/tenants/{tenant_id}", { tenant_id: t.id }, { is_active: !t.is_active }),
    onSuccess: refresh,
    onError: (e) => setErr(hubErrMsg(e)),
  });

  return (
    <div className="font-hub">
      <AdminTabs />
      <div className="max-w-[1100px]">
        <div className="text-[13px] text-hub-textSecondary mb-3">
          产品内提单（来源 <code>embedded</code>）的接入租户。每个租户一把 HMAC 密钥，租户服务端凭它为企业内个人签发门户凭证；
          提单人只能看到自己的工单（C/R/U，无删除）。
        </div>
        <form
          className="flex gap-2 items-start mb-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (code.trim() && name.trim()) create.mutate();
          }}
        >
          <input className={`${INPUT} w-[180px]`} value={code} onChange={(e) => setCode(e.target.value)} placeholder="租户编码（小写字母/数字/-_）" pattern="^[a-z0-9][a-z0-9_-]*$" />
          <input className={`${INPUT} w-[240px]`} value={name} onChange={(e) => setName(e.target.value)} placeholder="租户名称" />
          <button type="submit" className={BTN} disabled={create.isPending || !code.trim() || !name.trim()}>
            {create.isPending ? "创建中…" : "新建租户"}
          </button>
        </form>
        {err ? <div className="mb-3 text-[12px] text-hub-rose">{err}</div> : null}

        <table className="w-full text-[12.5px] border-collapse">
          <thead>
            <tr className="text-left text-hub-textMuted border-b border-hub-border">
              <th className="py-2 pr-3">编码</th>
              <th className="py-2 pr-3">名称</th>
              <th className="py-2 pr-3">状态</th>
              <th className="py-2 pr-3">提单人数</th>
              <th className="py-2 pr-3">工单数</th>
              <th className="py-2 pr-3">创建时间</th>
              <th className="py-2 pr-3">操作</th>
            </tr>
          </thead>
          <tbody>
            {(tenants.data ?? []).map((t) => (
              <tr key={t.id} className="border-b border-hub-borderLight">
                <td className="py-2 pr-3 font-mono">{t.code}</td>
                <td className="py-2 pr-3">{t.name}</td>
                <td className="py-2 pr-3">
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${t.is_active ? "bg-hub-green-light text-hub-green border-hub-green-border" : "bg-hub-neutral-light text-hub-textMuted border-hub-border"}`}>
                    {t.is_active ? "启用" : "停用"}
                  </span>
                </td>
                <td className="py-2 pr-3">{t.user_count}</td>
                <td className="py-2 pr-3">{t.ticket_count}</td>
                <td className="py-2 pr-3 font-mono text-[11px] text-hub-textFaint">{new Date(t.created_at).toLocaleString("zh-CN")}</td>
                <td className="py-2 pr-3 flex gap-2">
                  <button className={BTN_GHOST} onClick={() => rotate.mutate(t.id)} disabled={rotate.isPending}>
                    轮换密钥
                  </button>
                  <button className={BTN_GHOST} onClick={() => toggle.mutate(t)} disabled={toggle.isPending}>
                    {t.is_active ? "停用" : "启用"}
                  </button>
                </td>
              </tr>
            ))}
            {tenants.data && tenants.data.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-6 text-center text-hub-textFaint">
                  尚无租户
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {secret ? <SecretModal data={secret} onClose={() => setSecret(null)} /> : null}
    </div>
  );
}
