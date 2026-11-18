"""Real ONOS/Mininet Gymnasium environment. No simulated-network fallback."""
import json
from pathlib import Path
import urllib.error
import urllib.request
import numpy as np
import gymnasium as gym
from gymnasium import spaces
from rl_core import ACTIONS, observation, reward


class LiveEnv(gym.Env):
    metadata = {"render_modes": []}
    def __init__(self, evaluation=False, horizon=8):
        super().__init__()
        self.action_space = spaces.Discrete(4)
        self.observation_space = spaces.Box(0,1,shape=(14,),dtype=np.float32)
        self.token = Path(__file__).with_name(".rl-token").read_text().strip()
        self.evaluation = evaluation
        self.horizon = horizon
        self.paths = (0,0)
        self.utilization = [0.0]*8
        self.ready = False

    def request(self, endpoint, body=None):
        headers = {"Authorization":"Bearer "+self.token}
        raw = None
        if body is not None:
            headers["Content-Type"]="application/json"
            raw=json.dumps(body).encode()
        req=urllib.request.Request("http://127.0.0.1:8766"+endpoint,headers=headers,data=raw)
        try:
            with urllib.request.urlopen(req,timeout=180) as response:
                data=json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode()[:2000]) from exc
        return data

    def context(self):
        demands=(9,15,21) if self.evaluation else (6,12,18,24)
        background=(4,10,16) if self.evaluation else (0,8,12)
        self.demands=[int(self.np_random.choice(demands)) for _ in range(2)]
        self.bg_path=int(self.np_random.integers(2))
        self.bg_mbps=int(self.np_random.choice(background))

    def payload(self,action):
        return {"action":int(action),"demands":self.demands,
                "background_path":self.bg_path,"background_mbps":self.bg_mbps}

    def obs(self):
        return np.asarray(observation(self.demands,self.bg_path,self.bg_mbps,
                                     self.utilization,self.paths),dtype=np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.ready=False
        self.context()
        # Real startup trial obtains a measured initial observation; excluded
        # from PPO transitions and reported explicitly as reset warm-up.
        result=self.request("/step",self.payload(0))
        if result.get("status")!="MEASUREMENT_PASS":
            raise RuntimeError("Live reset measurement failed")
        self.paths=ACTIONS[0]
        self.utilization=result["utilization"]
        self.steps=0
        self.ready=True
        return self.obs(), {"reset_warmup":True,"evidence":result["evidence"]}

    def step(self,action):
        if not self.ready:
            raise RuntimeError("reset() required")
        if not self.action_space.contains(action):
            raise ValueError("Invalid action")
        result=self.request("/step",self.payload(action))
        if result.get("status")!="MEASUREMENT_PASS":
            raise RuntimeError("Live step measurement failed")
        new_paths=ACTIONS[int(action)]
        score=reward(result["receiver_mbps"],result["offered_mbps"],self.paths,new_paths)
        self.paths=new_paths
        self.utilization=result["utilization"]
        self.steps+=1
        truncated=self.steps>=self.horizon
        info=dict(result,reward=score,action=int(action),
                  background_path=self.bg_path,background_mbps=self.bg_mbps,
                  reward_definition="mean capped flow-demand satisfaction minus 0.02 assignment-change fraction",
                  observation_note="Next workload context plus previous trial measured utilization")
        if not truncated:
            self.context()
        else:
            self.ready=False
        return self.obs(),float(score),False,truncated,info
