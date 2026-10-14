import { FC, useState, useEffect } from "react";
import { PanelSection, PanelSectionRow, ButtonItem, TextField, ToggleField, Field } from "@decky/ui";
import { useRPCQuery, useRPCMutation } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import { useToast } from "../../hooks/useToast";

interface StatusShape {
  enabled: boolean;
  url: string;
  queued: number;
  installed: boolean;
}

interface DevServerCfg {
  enabled: boolean;
  url: string;
  token: string;
  status?: StatusShape;
}

export const DevServerSection: FC = () => {
  const toast = useToast();
  const q = useRPCQuery<[], DevServerCfg>(rpcRoutes.getDevServerConfig, []);
  const cfg = q.data;
  const [enabled, setEnabled] = useState<boolean>(false);
  const [url, setUrl] = useState<string>("");
  const [token, setToken] = useState<string>("");

  useEffect(() => {
    if (cfg) {
      setEnabled(cfg.enabled ?? false);
      setUrl(cfg.url ?? "");
      setToken(cfg.token ?? "");
    }
  }, [cfg]);

  const save = useRPCMutation<[boolean, string, string], DevServerCfg>(
    rpcRoutes.setDevServerConfig,
  );

  const doSave = async () => {
    const res = await save.mutate(enabled, url, token);
    if (res) toast.success("Dev server", res.status?.installed ? "connected" : "saved");
    else toast.error("Dev server", "save failed");
  };

  const showHelp = () => {
    toast.info("Dev panel", "Set localStorage key 'unifideck_dev'='1' in Steam client console to show this panel.");
  };

  return (
    <PanelSection title="Dev Server (temp)">
      <PanelSectionRow>
        <ToggleField label="Enabled" checked={enabled} onChange={setEnabled} />
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField label="URL" value={url} onChange={(e) => setUrl(e.target.value)} />
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField label="Token" value={token} onChange={(e) => setToken(e.target.value)} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => void doSave()}>
          {save.loading ? "Saving..." : "Save"}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={showHelp}>
          Help
        </ButtonItem>
      </PanelSectionRow>
      {cfg?.status && (
        <PanelSectionRow>
          <Field description={`queued=${cfg.status.queued} installed=${cfg.status.installed}`} bottomSeparator="none" />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
