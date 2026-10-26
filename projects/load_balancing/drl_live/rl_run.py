"""Live environment smoke test and short PPO integration training."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import torch
import gymnasium
import stable_baselines3
from stable_baselines3 import PPO
from rl_env import LiveEnv


def json_default(value):
    import numpy as np
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Unsupported JSON type: {type(value).__name__}")


def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=('smoke','train'))
    p.add_argument('--steps',type=int,default=64)
    p.add_argument('--seed',type=int,default=42)
    args=p.parse_args()
    if args.steps<16 or args.steps%16:
        p.error('--steps must be a positive multiple of 16 (minimum 16)')
    torch.set_num_threads(1)
    folder=Path('results/21-cn-load-balancing')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ-live-'+args.mode)
    folder.mkdir(parents=True)
    env=LiveEnv()
    versions={'python':platform.python_version(),'torch':torch.__version__,
              'gymnasium':gymnasium.__version__,'stable_baselines3':stable_baselines3.__version__}
    (folder/'versions.json').write_text(json.dumps(versions,indent=2))
    print('Adapter:',env.request('/health'),flush=True)
    if args.mode=='smoke':
        obs,info=env.reset(seed=args.seed)
        assert env.observation_space.contains(obs)
        print('Live reset: PASS',info,flush=True)
        for action in (0,1):
            obs,r,terminated,truncated,info=env.step(action)
            assert env.observation_space.contains(obs)
            assert not terminated and not truncated
            (folder/f'action-{action}.json').write_text(json.dumps(info,indent=2))
            print(json.dumps({'action':action,'status':info['status'],'paths':info['paths'],
                              'receiver_mbps':info['receiver_mbps'],
                              'loss_percent':info['loss_percent'],'reward':r,
                              'evidence':info['evidence']}),flush=True)
        print('LIVE_ENV_SMOKE_PASS; no PPO training performed in smoke mode.',flush=True)
    else:
        from stable_baselines3.common.callbacks import BaseCallback
        class LogSteps(BaseCallback):
            def _on_step(self):
                info=self.locals['infos'][0]
                with (folder/'transitions.jsonl').open('a') as f:
                    f.write(json.dumps(info, default=json_default)+'\n')
                print('Live transition',self.num_timesteps,'reward',info.get('reward'),flush=True)
                if self.num_timesteps % 64 == 0:
                    self.model.save(folder / f'checkpoint-{self.num_timesteps}')
                return True
        model=PPO('MlpPolicy',env,device='cpu',seed=args.seed,n_steps=16,
                  batch_size=16,n_epochs=5,policy_kwargs={'net_arch':[32,32]},verbose=1)
        model.learn(total_timesteps=args.steps,callback=LogSteps())
        model.save(folder/'ppo-live')
        PPO.load(folder/'ppo-live',device='cpu')
        print('LIVE_PPO_TRAIN_SAVE_RELOAD_PASS; held-out evaluation still required.',flush=True)
    print('Evidence:',folder.resolve(),flush=True)


if __name__=='__main__':
    main()
