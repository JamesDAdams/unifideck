import { FC, useState } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  TextField,
  ToggleField,
  Field,
} from "@decky/ui";

import { useRPCQuery, useRPCMutation } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import { useToast } from "../../hooks/useToast";

interface DevServerCfg {
  enabled: boolean;
  url: string;
  token: string;
  status?: {
    enabled: boolean;
    url: string;
    queued: number;
    installed: boolean;
  };
}

export const DevServerSection: FC = () => {
  const toast = useToast();
  const q = useRPCQuery<[], DevServerCfg>(rpcRoutes.getDevServerConfig, []);
  const cfg = q.data;
  const [enabled, setEnabled] = useState<boolean>(cfg?.enabled ?? false);
  const [url, setUrl] = useState<string>(cfg?.url ?? "");
  const [token, setToken] = useState<string>(cfg?.token ?? "");
  const save = useRPCMutation<[boolean, string, string], DevServerCfg>(
    rpcRoutes.setDevServerConfig,
  );

  // sync local state once loaded
  if (
    cfg &&
    enabled === false &&
    url === "" &&
    token === "" &&
    (cfg.enabled || cfg.url)
  ) {
    setEnabled(cfg.enabled);
    setUrl(cfg.url);
    setToken(cfg.token);
  }

  const doSave = async () => {
    const res = await save.mutate(enabled, url, token);
    if (res)
      toast.success(
        "Dev server",
        res.status?.installed ? "connected" : "saved",
      );
    else toast.error("Dev server", "save failed");
  };

  return (
    <PanelSection title="Dev Server (temp)">
      <PanelSectionRow>
        <ToggleField label="Enabled" checked={enabled} onChange={setEnabled} />
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="Token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => void doSave()}>
          {save.loading ? "Saving..." : "Save"}
        </ButtonItem>
      </PanelSectionRow>
      {cfg?.status && (
        <PanelSectionRow>
          <Field
            description={`queued=${cfg.status.queued} installed=${cfg.status.installed}`}
            bottomSeparator="none"
          />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
