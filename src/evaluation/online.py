import torch
from src.evaluation.offline import transform_action

class Evaluator:
    def __init__(self):
        self._track = {
            "lane_center_dist": [],
            "is_in_lane": [],
            "x_rel": [],
            "vx_rel": [],
            "loom_x": []
        }

    def observe(self, obs, ctl):
        left_bound_dist = obs[0, 2]
        right_bound_dist = obs[0, 3]
        lane_width = left_bound_dist + right_bound_dist
        lane_center_dist = left_bound_dist - lane_width / 2
        is_in_lane = 1 if left_bound_dist > 0 and right_bound_dist > 0 else 0
        self._track["lane_center_dist"].append(lane_center_dist.item())
        self._track["is_in_lane"].append(is_in_lane)
        self._track["x_rel"].append(obs[0, 4].item())
        self._track["vx_rel"].append(obs[0, 6].item())
        self._track["loom_x"].append(obs[0, 9].item())

def eval_episode(env, agent, eps_id, max_steps=1000, discretizers=None, callback=None):
    """ Evaluate episode
    
    Args:
        env (Env): gym style simulator
        agent (Agent): agent class
        eps_id (int): episode id
        max_steps (int, optional): maximum number of steps
        discretizers (list, optional): 
        callback (class, optional): controller callback

    Returns:
        sim_states (np.array): simulated states [T, num_agents, 5]
        sim_acts (np.array): simulated actions [T, 2]
        track_data (dict): recorded track data
        callback (class): updated callback. return if callback is not None
    """
    agent.eval()
    agent.reset()
    obs = env.reset(eps_id)

    rewards = []
    for t in range(max_steps):
        """ TODO: figure out a better way to choose recurrent action """
        with torch.no_grad():
            ctl, _ = agent.choose_action(obs.to(agent.device))
            ctl = ctl.cpu().data.view(1, -1)
            
        if discretizers is not None:
            ctl = transform_action(ctl, discretizers)
            
        obs, r, done, _ = env.step(ctl)
        if done or t >= max_steps:
            break
        rewards.append(r)
    
    return env._data, rewards