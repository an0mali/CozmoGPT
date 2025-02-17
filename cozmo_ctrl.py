
''' 
Used to parse and issue commands to cozmo, probably will control state machine eventually as well
'''
import cozmo
from cozmo.util import degrees, distance_mm, speed_mmps
import time


class Cozmo_Ctrl(object):

    def __init__(self, robot: cozmo.robot.Robot):
        self.robot = robot

        
        self.set_collision_event_handler()
        
        #Store collision sides here to be added to next prompt, clear after each prompt
        self.collisions_detected = []
        self.movements_made = []

        self.movement_memory = True

    def perform_action(self, action, units=0):
        #Parse an action and issue it to cozmo. Units are optional and only used for movement
        if action == "move":
            move_string = "You moved " + str(abs(units)) + " milimeters "
            direction = "foward, "
            if units <0:
                direction = "backward, "

            move_string += direction
            self.movements_made.append(move_string)
            
            self.cozmo_move(units, 50)
        elif action == "turn":
            direction = "left"
            if units < 0:
                direction = "right"
            self.movements_made.append(" You turned " + direction + " " + str(abs(units)) + " degrees.")
            self.cozmo_turn(units)
        elif action == "movehead":
            self.movements_made.append(" You Adjusted your head  " + str(units) + " degrees.")
            self.cozmo_headangle(units)
        else:
            print("Invalid action: ")

        #Some movements are not being executed, maybe a wait will help?
        time.sleep(0.1)

    def set_initial_pose(self):
        self.robot.set_head_angle(degrees(15)).wait_for_completed()
        self.robot.set_lift_height(0.0).wait_for_completed()

    def cozmo_move(self, distancemm, speedmmps=100):
        #Enable to detect collision, disable because he has random animations when enabeld
       
        self.robot.drive_straight(distance_mm(distancemm), speed_mmps(speedmmps), in_parallel=True).wait_for_completed()
        #Noticing sometimes valid movement actions aren't occuring, maybe sleep will help
        time.sleep(0.1)

    def cozmo_lift(self, position):
        if position == 0:
            self.robot.lift.lift_up().wait_for_completed()
        if position == 1:
            self.robot.lift.lift_down().wait_for_completed()

    def cozmo_turn(self, turn_degrees):
        #Postive 0-180 moves left, negative 0-180 moves right
        self.robot.turn_in_place(degrees(turn_degrees)).wait_for_completed()

    def cozmo_headangle(self, angle):
        self.robot.set_head_angle(degrees(angle)).wait_for_completed()

    def set_collision_event_handler(self):
        #Create an event handler that picks up on when cozmo collides into an object
        self.robot.add_event_handler(cozmo.robot.EvtUnexpectedMovement, self.handle_collision)

    def handle_collision(self, event: cozmo.robot.EvtUnexpectedMovement, **kwargs):
        #Print collision data to serial
        print(event.movement_side)
        #Parse collision data to feed back into promtp
        side = str(event.movement_side)
        side = side.replace("_UnexpectedMovementSide(name='", "")
        side = side.replace("', id=1)", "")
        print("Collisison side: " + side)
        if not side in self.collisions_detected:
            self.collisions_detected.append(side)
        #This seems to cancel further movements for a while, so lets wait
       # time.sleep(4)

       #Abort reactions such as frustration animation
        self.robot.abort_all_actions()

    def get_collision_prompt(self):
        if len(self.collisions_detected) < 1:
            return ""
        #Convert collision infromation to a prompt addition then clear array
        collide_prompt = "You are colliding with an object to your " 
        for side in self.collisions_detected:
            if len(self.collisions_detected) == 1:
                collide_prompt += side + "."
            else:
                collide_prompt += side + " and "
        self.collisions_detected = []
        #collide_prompt += " Earlier "
        #Add past movements to the promtp
        print(collide_prompt)
        return collide_prompt
        
    def get_movement_prompt(self):
        move_prompt = " You're in the same position as your were."
        if len(self.movements_made) > 0:
            move_prompt = "Previously "
        for movement in self.movements_made:
            if self.movement_memory:
                move_prompt += movement

        self.movements_made = []

        return move_prompt


            